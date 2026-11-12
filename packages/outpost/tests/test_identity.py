# SPDX-License-Identifier: Apache-2.0
"""An instance's keys are made on the spot and stay there (ADR 0147 §2)."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import stat
from pathlib import Path

import jwt
import pytest
from choregos_contracts import schemas_dir
from choregos_contracts.outpost import FINGERPRINT_PATTERN, PUBLIC_KEY_PATTERN, fingerprint_of
from choregos_outpost import identity as identities
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

SIGNED = json.loads(
    (schemas_dir() / "examples" / "outpost-signature.vector.json").read_text(encoding="utf-8")
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_the_keys_are_made_on_the_spot_in_files_only_the_daemon_reads(tmp_path: Path) -> None:
    directory = tmp_path / "state" / "identity"
    own = identities.create(directory)
    assert _mode(directory) == 0o700
    assert sorted(path.name for path in directory.iterdir()) == ["sealing.key", "signing.key"]
    assert _mode(directory / "signing.key") == 0o600 and _mode(directory / "sealing.key") == 0o600
    assert re.fullmatch(PUBLIC_KEY_PATTERN, own.signing_key) and re.fullmatch(
        PUBLIC_KEY_PATTERN, own.sealing_key
    )
    assert own.signing_key != own.sealing_key
    # Two instances never share a key: each makes its own.
    assert identities.create(tmp_path / "another").signing_key != own.signing_key
    # What is on disk is the key that signs: a restart is the same instance.
    again = identities.load(directory)
    assert (again.signing_key, again.sealing_key, again.fingerprint) == (
        own.signing_key,
        own.sealing_key,
        own.fingerprint,
    )
    signature = again.sign({"htm": "POST"}, {"alg": "EdDSA", "typ": "choregos-outpost+jwt"})
    public = Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(own.signing_key + "="))
    assert jwt.decode(signature, public, algorithms=["EdDSA"]) == {"htm": "POST"}


def test_the_fingerprint_is_the_contracts_and_is_computed_from_the_instances_own_key(tmp_path: Path) -> None:
    own = identities.create(tmp_path / "identity")
    raw = base64.urlsafe_b64decode(own.signing_key + "=")
    digest = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
    assert own.fingerprint == "SHA256:" + digest == fingerprint_of(own.signing_key)
    assert re.fullmatch(FINGERPRINT_PATTERN, own.fingerprint)
    # The contract's vector: the key its label gives has the fingerprint written there.
    seed = hashlib.sha256(SIGNED["signing_key_label"].encode()).digest()
    vector = identities.Identity(Ed25519PrivateKey.from_private_bytes(seed), X25519PrivateKey.generate())
    assert vector.signing_key == SIGNED["signing_key"] and vector.fingerprint == SIGNED["fingerprint"]


@pytest.mark.parametrize(
    ("mode", "refused"),
    [
        (0o600, False),
        (0o400, False),
        (0o640, False),
        (0o644, True),
        (0o604, True),
        (0o660, True),
        (0o666, True),
    ],
    ids=[
        "owner",
        "owner read-only",
        "the owner's group may read",
        "others read",
        "others read, not the group",
        "the group writes",
        "everyone writes",
    ],
)
def test_a_key_file_open_to_others_is_refused(tmp_path: Path, mode: int, refused: bool) -> None:
    directory = tmp_path / "identity"
    identities.create(directory)
    (directory / "signing.key").chmod(mode)
    if refused:
        with pytest.raises(identities.IdentityError, match="open to others"):
            identities.load(directory)
    else:
        identities.load(directory)


def test_an_identity_is_never_replaced_and_never_half_made(tmp_path: Path) -> None:
    directory = tmp_path / "identity"
    own = identities.create(directory)
    with pytest.raises(identities.IdentityError, match="not replaced"):
        identities.create(directory)
    same, new = identities.load_or_create(directory)
    assert not new and same.fingerprint == own.fingerprint
    made, new = identities.load_or_create(tmp_path / "fresh")
    assert new and made.fingerprint != own.fingerprint
    (directory / "sealing.key").unlink()
    with pytest.raises(identities.IdentityError, match="one key of two"):
        identities.load(directory)
    with pytest.raises(identities.IdentityError, match="choregos-outpost enrol"):
        identities.load(tmp_path / "nowhere")


def test_a_file_that_is_not_the_right_key_is_refused_without_being_quoted(tmp_path: Path) -> None:
    directory = tmp_path / "identity"
    identities.create(directory)
    signing = (directory / "signing.key").read_bytes()
    (directory / "sealing.key").write_bytes(signing)  # an Ed25519 key where the X25519 one belongs
    with pytest.raises(identities.IdentityError, match="X25519") as wrong:
        identities.load(directory)
    assert signing.decode().splitlines()[1] not in str(wrong.value)
    (directory / "sealing.key").write_text("not a key at all", encoding="utf-8")
    with pytest.raises(identities.IdentityError, match="not a PEM private key") as garbage:
        identities.load(directory)
    assert "not a key at all" not in str(garbage.value)


def test_nothing_the_identity_shows_holds_a_private_key(tmp_path: Path) -> None:
    directory = tmp_path / "identity"
    own = identities.create(directory)
    identities.save_enrolment(
        directory,
        identities.Enrolment("instance-1", "acme", "factory-floor", "https://outposts.example.com", "now"),
    )
    assert identities.load_enrolment(directory) == identities.Enrolment(
        "instance-1", "acme", "factory-floor", "https://outposts.example.com", "now"
    )
    shown = repr(own) + str(own) + repr(vars(own)) + (directory / "instance.json").read_text(encoding="utf-8")
    for name in ("signing.key", "sealing.key"):
        pem = (directory / name).read_bytes()
        key = serialization.load_pem_private_key(pem, password=None)
        raw = key.private_bytes(  # type: ignore[union-attr]
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        for spelling in (
            pem.decode().splitlines()[1],
            raw.hex(),
            base64.b64encode(raw).decode(),
            base64.urlsafe_b64encode(raw).decode().rstrip("="),
        ):
            assert spelling not in shown
    assert own.fingerprint in repr(own)
    assert _mode(directory / "instance.json") == 0o600
    assert identities.load_enrolment(tmp_path / "nowhere") is None
