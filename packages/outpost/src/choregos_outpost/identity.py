# SPDX-License-Identifier: Apache-2.0
"""An instance's identity: two key pairs it made on the spot (ADR 0147 §2).

At its first start an instance generates an Ed25519 signing key and an X25519 sealing key. The
private halves never leave the machine: they are written to files only the daemon reads, they are
never sent and never logged. Enrolment sends the public halves, and a person compares the
fingerprint of the signing key (ADR 0147 §4).

Where it lives, under the identity directory (`<state dir>/identity` unless said otherwise):

- `signing.key`: the Ed25519 private key, PEM (PKCS #8), mode 0600;
- `sealing.key`: the X25519 private key, PEM (PKCS #8), mode 0600;
- `instance.json`: what the door answered at enrolment (the instance's id, its organisation and
  outpost, the door it enrolled at). No secret is in it.

The directory is created 0700. A key file that anyone outside the owner's group can read, or that
the group can write, is refused: the daemon does not start on a key it cannot call its own. A key
readable by the owner's group is accepted, because a volume mounted for a container's group is
exactly that.

This object never hands out private bytes: it signs and it opens, and its `repr` shows the
fingerprint.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import jwt
from choregos_adapters import sealing
from choregos_contracts.outpost import SIGNATURE_ALGORITHM, fingerprint_of
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

SIGNING_FILE = "signing.key"
SEALING_FILE = "sealing.key"
ENROLMENT_FILE = "instance.json"
#: What a key file may not grant: any access to others, writing to the group.
_TOO_OPEN = stat.S_IRWXO | stat.S_IWGRP
_RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)


class IdentityError(Exception):
    """The identity cannot be used as it is on disk; the message says what to do about it."""


def _unpadded(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).decode().rstrip("=")


@dataclass(frozen=True)
class Enrolment:
    """What the door answered at enrolment, kept beside the keys. Nothing here is a secret."""

    instance_id: str
    organisation: str
    outpost: str
    control_plane: str
    enrolled_at: str


class Identity:
    """The two private keys of one instance, and the only things done with them."""

    def __init__(self, signing: Ed25519PrivateKey, sealing_key: X25519PrivateKey) -> None:
        self._signing = signing
        self._sealing = sealing_key

    @property
    def signing_key(self) -> str:
        """The public half of the signing key: raw, base64url without padding."""
        return _unpadded(self._signing.public_key().public_bytes(*_RAW))

    @property
    def sealing_key(self) -> str:
        """The public half of the sealing key, written the same way."""
        return _unpadded(self._sealing.public_key().public_bytes(*_RAW))

    @property
    def fingerprint(self) -> str:
        """`SHA256:` and the digest of the raw signing key, as the contract writes it. It is computed
        from the instance's own key, never read from an answer of the door."""
        return fingerprint_of(self.signing_key)

    def sign(self, claims: dict[str, Any], header: dict[str, str]) -> str:
        """A compact JWS over `claims`, made with the signing key (EdDSA)."""
        return jwt.encode(claims, self._signing, algorithm=SIGNATURE_ALGORITHM, headers=header)

    def open(self, call_id: str, sealed: str) -> dict[str, Any]:
        """What an envelope sealed to this instance holds; `sealing.SealError` when it does not open."""
        return sealing.open_sealed(self._sealing, call_id, sealed)

    def __repr__(self) -> str:
        return f"Identity(fingerprint={self.fingerprint!r})"


def _private_pem(private: Ed25519PrivateKey | X25519PrivateKey) -> bytes:
    return private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def _write_private(path: Path, data: bytes) -> None:
    """A new file only its owner reads; an existing one is never replaced."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
    except BaseException:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def _read_private(path: Path) -> bytes:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as error:
        raise IdentityError(f"{path}: {error.strerror or error}") from error
    if mode & _TOO_OPEN:
        raise IdentityError(
            f"{path} is open to others (mode {mode:04o}): a private key is read by the daemon only "
            f"(chmod 600 {path})"
        )
    try:
        return path.read_bytes()
    except OSError as error:
        raise IdentityError(f"{path}: {error.strerror or error}") from error


def exists(directory: Path) -> bool:
    """Whether the directory already holds a key of an instance."""
    return (directory / SIGNING_FILE).exists() or (directory / SEALING_FILE).exists()


def create(directory: Path) -> Identity:
    """Makes the two key pairs and writes their private halves; refuses to replace existing ones."""
    if exists(directory):
        raise IdentityError(f"{directory} already holds an identity: it is not replaced")
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        identity = Identity(Ed25519PrivateKey.generate(), X25519PrivateKey.generate())
        _write_private(directory / SIGNING_FILE, _private_pem(identity._signing))
        _write_private(directory / SEALING_FILE, _private_pem(identity._sealing))
    except OSError as error:
        raise IdentityError(f"{directory}: the keys cannot be written ({error.strerror or error})") from error
    return identity


def load(directory: Path) -> Identity:
    """The identity a former `enrol` wrote; `IdentityError` when it is absent, partial or exposed."""
    signing_path, sealing_path = directory / SIGNING_FILE, directory / SEALING_FILE
    if not signing_path.exists() and not sealing_path.exists():
        raise IdentityError(f"{directory} holds no identity: run `choregos-outpost enrol` first")
    if not signing_path.exists() or not sealing_path.exists():
        raise IdentityError(f"{directory} holds one key of two: restore the other, or enrol a new instance")
    try:
        signing = serialization.load_pem_private_key(_read_private(signing_path), password=None)
        sealing_key = serialization.load_pem_private_key(_read_private(sealing_path), password=None)
    except (ValueError, TypeError) as error:
        # The library's message may quote the file: say only which directory does not parse.
        raise IdentityError(f"{directory}: a key file is not a PEM private key") from error
    if not isinstance(signing, Ed25519PrivateKey) or not isinstance(sealing_key, X25519PrivateKey):
        raise IdentityError(
            f"{directory}: `{SIGNING_FILE}` must be an Ed25519 key and `{SEALING_FILE}` an X25519 key"
        )
    return Identity(signing, sealing_key)


def load_or_create(directory: Path) -> tuple[Identity, bool]:
    """The identity of this directory, made now if there is none: (identity, whether it is new).

    An instance whose enrolment answer was lost enrols again with the keys it has (ADR 0148 §3)."""
    if exists(directory):
        return load(directory), False
    return create(directory), True


def save_enrolment(directory: Path, enrolment: Enrolment) -> None:
    path = directory / ENROLMENT_FILE
    temporary = path.with_suffix(".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(asdict(enrolment), file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    except OSError as error:
        raise IdentityError(f"{path}: the enrolment cannot be written ({error.strerror or error})") from error


def load_enrolment(directory: Path) -> Enrolment | None:
    """What the door answered at enrolment, or None when this identity never enrolled."""
    path = directory / ENROLMENT_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Enrolment(**{name: str(data[name]) for name in Enrolment.__dataclass_fields__})
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise IdentityError(f"{path} is not an enrolment record: enrol the instance again") from error
