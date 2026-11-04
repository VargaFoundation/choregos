# SPDX-License-Identifier: Apache-2.0
"""Connector secrets typed in the console: write-only, sealed in the database (ADR 0058).

Three layers:

- `seal` / `unseal`: envelope encryption. A value is encrypted with a random data key (AES-256-GCM);
  the data key is wrapped under a key-encryption key (KEK) that the DEPLOYMENT holds
  (`CHOREGOS_SECRETS_KEKS="k2:<b64 32 bytes>,k1:<b64 32 bytes>"`, the first is active). The
  additional authenticated data (`org_id|name|version`) binds a row to its place: a ciphertext
  copied to another organisation or name does not open.
- `KeyWrapper`: wraps the data keys. `EnvKeyWrapper` (the keyring above) is the default; an
  extension can supply a KMS or Vault-transit wrapper (ADR 0103).
- `SecretStore`: the seam. `SealedDbSecretStore` (the table `org_secrets`) is the default;
  `FakeSecretStore` serves `CHOREGOS_FAKES` when no key is configured.
- `resolved_configuration`: what every call site that builds an adapter uses. It reads the
  `store:` references with the organisation's context, then hands the rest to the sync core.

A secret is never returned by an API, never written in the audit log, and the values the process
resolves are remembered by `choregos_core.redaction` so that logs and `last_error` mask them.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from choregos_core import utcnow
from choregos_core.redaction import remember_secret
from choregos_core.secrets import (
    STORE_SCHEME,
    SecretIntrouvable,
    is_store_reference,
)
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import OrgSecret
from .schemas import ConnectorSecretState

ENV_KEKS = "CHOREGOS_SECRETS_KEKS"
ALGORITHM = "A256GCM"
NO_KEY_MESSAGE = f"no secrets key configured ({ENV_KEKS}): use a reference, or ask the operator"

_NAME = re.compile(r"^[a-z0-9][a-z0-9/_.-]{0,199}$")
_KEK_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class SecretStoreUnavailable(RuntimeError):  # noqa: N818 - a named lack, rendered as is (409)
    """The store cannot seal or unseal: no key, or the row's key is not in the keyring."""


# ───────────────────────────────── the envelope ─────────────────────────────────


@dataclass(frozen=True)
class Keyring:
    """The KEKs of the deployment. `active` seals; every key opens what it sealed."""

    keys: Mapping[str, bytes]
    active_id: str

    @property
    def active(self) -> bytes:
        return self.keys[self.active_id]

    def get(self, kek_id: str) -> bytes:
        try:
            return self.keys[kek_id]
        except KeyError:
            raise SecretStoreUnavailable(
                f"the secret is sealed under the key `{kek_id}`, which {ENV_KEKS} no longer lists"
            ) from None


def parse_keyring(raw: str | None) -> Keyring | None:
    """`k2:<base64 of 32 bytes>,k1:<...>`: the first key is the active one. None when unset;
    a malformed value is an error that names the problem, never a silent downgrade."""
    if not raw or not raw.strip():
        return None
    keys: dict[str, bytes] = {}
    for part in raw.split(","):
        kek_id, sep, encoded = part.strip().partition(":")
        if not sep or not _KEK_ID.match(kek_id):
            raise SecretStoreUnavailable(
                f"{ENV_KEKS}: expected `id:base64`, with an id of [A-Za-z0-9_-]{{1,32}}"
            )
        try:
            key = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise SecretStoreUnavailable(f"{ENV_KEKS}: the key `{kek_id}` is not valid base64") from None
        if len(key) != 32:
            raise SecretStoreUnavailable(f"{ENV_KEKS}: the key `{kek_id}` must be 32 bytes, not {len(key)}")
        if kek_id in keys:
            raise SecretStoreUnavailable(f"{ENV_KEKS}: the key id `{kek_id}` is listed twice")
        keys[kek_id] = key
    return Keyring(keys=keys, active_id=next(iter(keys)))


def keyring_from_env() -> Keyring | None:
    return parse_keyring(os.environ.get(ENV_KEKS))


@dataclass(frozen=True)
class Sealed:
    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    dek_nonce: bytes
    kek_id: str


def _aad(org_id: str, name: str, version: int) -> bytes:
    return f"{org_id}|{name}|{version}".encode()


class KeyWrapper(Protocol):
    """Wraps and unwraps the data keys (DEKs). The environment keyring is the default; an
    extension can supply a KMS or Vault-transit wrapper (ADR 0103). Core ships no KMS client."""

    active_id: str

    def wrap(self, dek: bytes, aad: bytes) -> tuple[bytes, bytes, str]:
        """Returns `(wrapped, nonce, kek_id)`; `kek_id` is at most 32 characters."""
        ...

    def unwrap(self, wrapped: bytes, nonce: bytes, aad: bytes, kek_id: str) -> bytes:
        """Raises `SecretStoreUnavailable` when the key is unknown or the envelope does not open."""
        ...


class EnvKeyWrapper:
    """The default wrapper: AES-256-GCM under the keyring of `CHOREGOS_SECRETS_KEKS`."""

    def __init__(self, keyring: Keyring) -> None:
        self._keyring = keyring
        self.active_id = keyring.active_id

    def wrap(self, dek: bytes, aad: bytes) -> tuple[bytes, bytes, str]:
        nonce = os.urandom(12)
        wrapped = AESGCM(self._keyring.active).encrypt(nonce, dek, b"dek|" + aad)
        return wrapped, nonce, self._keyring.active_id

    def unwrap(self, wrapped: bytes, nonce: bytes, aad: bytes, kek_id: str) -> bytes:
        try:
            return AESGCM(self._keyring.get(kek_id)).decrypt(nonce, wrapped, b"dek|" + aad)
        except InvalidTag:
            raise SecretStoreUnavailable("the sealed key does not open (wrong key, or moved)") from None


def _as_wrapper(wrapper: KeyWrapper | Keyring) -> KeyWrapper:
    return EnvKeyWrapper(wrapper) if isinstance(wrapper, Keyring) else wrapper


def seal(value: str, wrapper: KeyWrapper | Keyring, org_id: str, name: str, version: int) -> Sealed:
    aad = _aad(org_id, name, version)
    dek, nonce = AESGCM.generate_key(256), os.urandom(12)
    wrapped_dek, dek_nonce, kek_id = _as_wrapper(wrapper).wrap(dek, aad)
    return Sealed(
        ciphertext=AESGCM(dek).encrypt(nonce, value.encode(), aad),
        nonce=nonce,
        wrapped_dek=wrapped_dek,
        dek_nonce=dek_nonce,
        kek_id=kek_id,
    )


def _open_dek(row: OrgSecret, wrapper: KeyWrapper | Keyring) -> bytes:
    aad = _aad(row.org_id, row.name, row.version)
    try:
        return _as_wrapper(wrapper).unwrap(row.wrapped_dek, row.dek_nonce, aad, row.kek_id)
    except SecretStoreUnavailable as refus:
        if "does not open" in str(refus):
            raise SecretStoreUnavailable(
                f"`{row.name}`: the sealed key does not open (wrong key, or moved)"
            ) from None
        raise


def unseal(row: OrgSecret, wrapper: KeyWrapper | Keyring) -> str:
    dek = _open_dek(row, wrapper)
    try:
        return (
            AESGCM(dek).decrypt(row.nonce, row.ciphertext, _aad(row.org_id, row.name, row.version)).decode()
        )
    except InvalidTag:
        raise SecretStoreUnavailable(
            f"`{row.name}`: the sealed value does not open (tampered, or moved)"
        ) from None


# ───────────────────────────────── the seam ─────────────────────────────────


@dataclass(frozen=True)
class SecretMeta:
    name: str
    version: int
    rotated_at: datetime
    rotated_by: str | None


class SecretStore(Protocol):
    """Where a typed secret lives. Never returns a value except to `get` / `get_many`, which only
    the platform's own call sites use to build an adapter."""

    def available(self) -> bool: ...

    async def put(
        self, session: AsyncSession, org_id: str, name: str, value: str, actor: str | None
    ) -> SecretMeta: ...

    async def get_many(self, session: AsyncSession, org_id: str, names: Sequence[str]) -> dict[str, str]: ...

    async def meta(
        self, session: AsyncSession, org_id: str, names: Sequence[str]
    ) -> dict[str, SecretMeta]: ...

    async def delete(self, session: AsyncSession, org_id: str, name: str) -> bool: ...


def check_name(name: str) -> str:
    if not _NAME.match(name):
        raise ValueError(f"invalid secret name: {name!r}")
    return name


class SealedDbSecretStore:
    """The default: AES-256-GCM envelopes in `org_secrets`, KEKs from the deployment."""

    def __init__(self, keyring: Keyring | None = None, wrapper: KeyWrapper | None = None) -> None:
        self._keyring = keyring
        self._wrapper_given = wrapper

    def _wrapper(self) -> KeyWrapper | None:
        if self._wrapper_given is not None:
            return self._wrapper_given
        keyring = self._keyring or keyring_from_env()
        return EnvKeyWrapper(keyring) if keyring is not None else None

    def available(self) -> bool:
        return self._wrapper() is not None

    async def put(
        self, session: AsyncSession, org_id: str, name: str, value: str, actor: str | None
    ) -> SecretMeta:
        wrapper = self._wrapper()
        if wrapper is None:
            raise SecretStoreUnavailable(NO_KEY_MESSAGE)
        check_name(name)
        row = await _row(session, org_id, name)
        now = utcnow()
        version = row.version + 1 if row is not None else 1
        sealed = seal(value, wrapper, org_id, name, version)
        if row is None:
            row = OrgSecret(org_id=org_id, name=name, created_by=actor)
            session.add(row)
        row.ciphertext, row.nonce = sealed.ciphertext, sealed.nonce
        row.wrapped_dek, row.dek_nonce, row.kek_id = sealed.wrapped_dek, sealed.dek_nonce, sealed.kek_id
        row.algorithm, row.version, row.updated_by, row.rotated_at = ALGORITHM, version, actor, now
        await session.flush()
        return SecretMeta(name, version, now, actor)

    async def get_many(self, session: AsyncSession, org_id: str, names: Sequence[str]) -> dict[str, str]:
        if not names:
            return {}
        wrapper = self._wrapper()
        if wrapper is None:
            raise SecretIntrouvable(NO_KEY_MESSAGE)
        rows = {r.name: r for r in await _rows(session, org_id, names)}
        out: dict[str, str] = {}
        for name in names:
            row = rows.get(name)
            if row is None:
                raise SecretIntrouvable(
                    f"`{STORE_SCHEME}:{name}`: no value is stored (cleared, or never set)"
                )
            try:
                out[name] = unseal(row, wrapper)
            except SecretStoreUnavailable as refus:
                raise SecretIntrouvable(str(refus)) from refus
        return out

    async def meta(self, session: AsyncSession, org_id: str, names: Sequence[str]) -> dict[str, SecretMeta]:
        if not names:
            return {}
        return {r.name: _meta(r) for r in await _rows(session, org_id, names)}

    async def delete(self, session: AsyncSession, org_id: str, name: str) -> bool:
        row = await _row(session, org_id, name)
        if row is None:
            return False
        await session.delete(row)
        await session.flush()
        return True

    async def rewrap(self, session: AsyncSession, org_id: str | None = None) -> int:
        """Re-wrap the data keys under the active KEK (after a rotation: the new key first in the
        list). The VALUES are never touched, only their wrapping. Returns the rows changed."""
        wrapper = self._wrapper()
        if wrapper is None:
            raise SecretStoreUnavailable(NO_KEY_MESSAGE)
        query = select(OrgSecret).where(OrgSecret.kek_id != wrapper.active_id)
        if org_id is not None:
            query = query.where(OrgSecret.org_id == org_id)
        changed = 0
        for row in (await session.execute(query)).scalars():
            dek = _open_dek(row, wrapper)
            aad = _aad(row.org_id, row.name, row.version)
            row.wrapped_dek, row.dek_nonce, row.kek_id = wrapper.wrap(dek, aad)
            changed += 1
        await session.flush()
        return changed


async def _row(session: AsyncSession, org_id: str, name: str) -> OrgSecret | None:
    return (
        await session.execute(select(OrgSecret).where(OrgSecret.org_id == org_id, OrgSecret.name == name))
    ).scalar_one_or_none()


async def _rows(session: AsyncSession, org_id: str, names: Sequence[str]) -> list[OrgSecret]:
    return list(
        (
            await session.execute(
                select(OrgSecret).where(OrgSecret.org_id == org_id, OrgSecret.name.in_(names))
            )
        ).scalars()
    )


def _meta(row: OrgSecret) -> SecretMeta:
    return SecretMeta(row.name, row.version, row.rotated_at, row.updated_by)


class FakeSecretStore:
    """In memory, scriptable: `CHOREGOS_FAKES` without a key, and the tests of the seam."""

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.values: dict[tuple[str, str], tuple[str, int, datetime, str | None]] = {}

    def available(self) -> bool:
        return self._available

    async def put(
        self, session: AsyncSession, org_id: str, name: str, value: str, actor: str | None
    ) -> SecretMeta:
        if not self._available:
            raise SecretStoreUnavailable(NO_KEY_MESSAGE)
        check_name(name)
        version = self.values[(org_id, name)][1] + 1 if (org_id, name) in self.values else 1
        now = utcnow()
        self.values[(org_id, name)] = (value, version, now, actor)
        return SecretMeta(name, version, now, actor)

    async def get_many(self, session: AsyncSession, org_id: str, names: Sequence[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for name in names:
            if (org_id, name) not in self.values:
                raise SecretIntrouvable(
                    f"`{STORE_SCHEME}:{name}`: no value is stored (cleared, or never set)"
                )
            out[name] = self.values[(org_id, name)][0]
        return out

    async def meta(self, session: AsyncSession, org_id: str, names: Sequence[str]) -> dict[str, SecretMeta]:
        return {
            n: SecretMeta(n, v[1], v[2], v[3])
            for n in names
            if (v := self.values.get((org_id, n))) is not None
        }

    async def delete(self, session: AsyncSession, org_id: str, name: str) -> bool:
        return self.values.pop((org_id, name), None) is not None


_FAKE = FakeSecretStore()
_OVERRIDE: list[SecretStore] = []


def set_secret_store(store: SecretStore | None) -> None:
    """For tests and plugins: replace the store of the process (None restores the default)."""
    _OVERRIDE.clear()
    if store is not None:
        _OVERRIDE.append(store)


def get_secret_store() -> SecretStore:
    """The sealed database store; under `CHOREGOS_FAKES` and without a key, the in-memory fake."""
    if _OVERRIDE:
        return _OVERRIDE[0]
    store = SealedDbSecretStore()
    if not store.available() and os.environ.get("CHOREGOS_FAKES", "") in {"1", "true", "yes"}:
        return _FAKE
    return store


def reset_fake_store() -> None:
    _FAKE.values.clear()


# ───────────────────────────── names, states, resolution ─────────────────────────────


def derived_org_name(connector: str, field: str) -> str:
    return f"connector/{connector}/{field}"


def derived_project_name(project_slug: str, kind: str, field: str) -> str:
    return f"project/{project_slug}/{kind}/{field}"


def store_ref(name: str) -> str:
    return f"{STORE_SCHEME}:{name}"


def store_names(references: Mapping[str, str]) -> dict[str, str]:
    """field -> secret name, for the references that point to the store."""
    return {f: r[len(STORE_SCHEME) + 1 :] for f, r in references.items() if is_store_reference(r)}


async def secret_states(
    session: AsyncSession,
    org_id: str,
    references: Mapping[str, str],
    store: SecretStore | None = None,
) -> list[ConnectorSecretState]:
    """One state per configured secret field, for the DTO: no value, ever."""
    from choregos_adapters.registry import slot_of

    names = store_names(references)
    metas = await (store or get_secret_store()).meta(session, org_id, list(names.values())) if names else {}
    states: list[ConnectorSecretState] = []
    # `api_key`, `api_key.2` … `api_key.10` in slot order, not in text order (ADR 0089).
    for field in sorted(references, key=slot_of):
        if field in names:
            meta = metas.get(names[field])
            states.append(
                ConnectorSecretState(
                    field=field,
                    source="store",
                    version=meta.version if meta else None,
                    rotated_at=meta.rotated_at if meta else None,
                    rotated_by=meta.rotated_by if meta else None,
                )
            )
        else:
            states.append(ConnectorSecretState(field=field, source="reference", reference=references[field]))
    return states


async def resolve_store_fields(
    session: AsyncSession,
    org_id: str,
    kind: str,
    type_name: str,
    secret_refs: Mapping[str, str] | None,
    secret_ref: str | None = None,
    store: SecretStore | None = None,
) -> dict[str, str]:
    """field -> value for the `store:` references of a connector, read with the organisation's
    context in one query. A missing value raises `SecretIntrouvable`, naming the reference."""
    from choregos_adapters.registry import secret_references

    names = store_names(secret_references(kind, type_name, dict(secret_refs or {}), secret_ref))
    if not names:
        return {}
    values = await (store or get_secret_store()).get_many(session, org_id, list(names.values()))
    for value in values.values():
        remember_secret(value)
    return {field: values[name] for field, name in names.items()}


async def resolved_configuration(
    session: AsyncSession,
    org_id: str,
    kind: str,
    type_name: str,
    config: dict[str, Any] | None,
    secret_refs: Mapping[str, str] | None,
    secret_ref: str | None = None,
    store: SecretStore | None = None,
) -> dict[str, Any]:
    """The configuration with its secrets RESOLVED — `store:` through the store, the rest through
    the core's resolvers. To build an adapter, never to save or return. Every resolved value is
    remembered by the redaction, so that a log line or an error text cannot carry it out."""
    from choregos_adapters.registry import resolved_configuration, secret_references, slot_of

    from .services.github_app import pin_installation

    # A GitHub connector builds with the installation its organisation claimed (ADR 0073).
    config = await pin_installation(session, org_id, kind, type_name, dict(config or {}))
    preresolved = await resolve_store_fields(session, org_id, kind, type_name, secret_refs, secret_ref, store)
    resolved = resolved_configuration(
        kind, type_name, config, dict(secret_refs or {}), secret_ref, preresolved=preresolved
    )
    for name in secret_references(kind, type_name, dict(secret_refs or {}), secret_ref):
        value = resolved.get(slot_of(name)[0])
        for one in value if isinstance(value, list) else [value]:
            if isinstance(one, str):
                remember_secret(one)
    return resolved


__all__ = [
    "ENV_KEKS",
    "NO_KEY_MESSAGE",
    "EnvKeyWrapper",
    "FakeSecretStore",
    "KeyWrapper",
    "Keyring",
    "SealedDbSecretStore",
    "SecretMeta",
    "SecretStore",
    "SecretStoreUnavailable",
    "derived_org_name",
    "derived_project_name",
    "get_secret_store",
    "keyring_from_env",
    "parse_keyring",
    "resolve_store_fields",
    "resolved_configuration",
    "seal",
    "secret_states",
    "set_secret_store",
    "store_names",
    "store_ref",
    "unseal",
]
