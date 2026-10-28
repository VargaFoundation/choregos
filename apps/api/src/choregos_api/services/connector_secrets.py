# SPDX-License-Identifier: Apache-2.0
"""What a connector's secret fields do when a person sets or clears one (ADR 0058).

One place for org connectors and project connectors: a typed value is sealed in the store and the
connector keeps only a DERIVED `store:` reference; clearing removes both. The caller owns the
permission check and the audit record (it knows the principal and the target)."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from ..errors import conflict
from ..secret_store import (
    SecretMeta,
    SecretStore,
    SecretStoreUnavailable,
    get_secret_store,
    store_names,
    store_ref,
)

#: A secret field of a type, or a slot of a field it pools: `api_key.2` … `api_key.10` (ADR 0089).
#: The route accepts the shape; `verifier_les_secrets` says whether this type has this slot.
SECRET_FIELD = r"^[a-z][a-z0-9_]{0,63}(\.([2-9]|10))?$"  # noqa: S105 - a pattern, not a secret


def require_store(values: Mapping[str, str] | None) -> SecretStore:
    """The store, or 409 naming what is missing — checked BEFORE anything is written."""
    store = get_secret_store()
    if values and not store.available():
        from ..secret_store import NO_KEY_MESSAGE

        raise conflict(NO_KEY_MESSAGE)
    return store


async def put_values(
    session: AsyncSession,
    store: SecretStore,
    org_id: str,
    prefix: str,
    values: Mapping[str, str],
    actor: str | None,
) -> dict[str, SecretMeta]:
    """Seal each value under `<prefix>/<field>`; the metas by field."""
    metas: dict[str, SecretMeta] = {}
    for field, value in values.items():
        try:
            metas[field] = await store.put(session, org_id, f"{prefix}/{field}", value, actor)
        except SecretStoreUnavailable as refus:
            raise conflict(str(refus)) from refus
    return metas


def refs_with_values(
    refs: Mapping[str, str] | None, prefix: str, fields: Mapping[str, str]
) -> dict[str, str]:
    """The references once `fields` are stored: each field now points to its derived name."""
    merged = dict(refs or {})
    for field in fields:
        merged[field] = store_ref(f"{prefix}/{field}")
    return merged


async def drop_replaced_store_values(
    session: AsyncSession,
    store: SecretStore,
    org_id: str,
    before: Mapping[str, str] | None,
    after: Mapping[str, str],
) -> list[str]:
    """A field that moved from a stored value to a reference (or vanished) leaves a sealed value no
    reference points to: delete it. Returns the fields cleared."""
    cleared: list[str] = []
    after_names = set(store_names(after).values())
    for field, name in store_names(before or {}).items():
        if name not in after_names:
            await store.delete(session, org_id, name)
            cleared.append(field)
    return cleared
