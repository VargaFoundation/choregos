"""The sealed secret store (S39-06c, ADR 0058): the envelope, the keyring, the seam."""

from __future__ import annotations

import base64
import os
from typing import Any

import pytest
from choregos_api.secret_store import (
    ENV_KEKS,
    FakeSecretStore,
    SealedDbSecretStore,
    SecretStoreUnavailable,
    get_secret_store,
    parse_keyring,
    resolved_configuration,
    seal,
    set_secret_store,
    unseal,
)
from choregos_core.redaction import forget_secrets, redact
from choregos_core.secrets import SecretIntrouvable


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _ring(*ids: str) -> Any:
    ring = parse_keyring(",".join(f"{i}:{_key()}" for i in ids))
    assert ring is not None
    return ring


def _row(sealed: Any, org: str, name: str, version: int) -> Any:
    from choregos_api.db.models import OrgSecret

    return OrgSecret(
        org_id=org,
        name=name,
        version=version,
        ciphertext=sealed.ciphertext,
        nonce=sealed.nonce,
        wrapped_dek=sealed.wrapped_dek,
        dek_nonce=sealed.dek_nonce,
        kek_id=sealed.kek_id,
    )


def test_a_sealed_value_round_trips_and_is_not_in_the_row() -> None:
    ring = _ring("k1")
    sealed = seal("s3cr3t-value", ring, "org-1", "connector/jira/api_token", 1)
    assert b"s3cr3t-value" not in sealed.ciphertext and b"s3cr3t-value" not in sealed.wrapped_dek
    assert unseal(_row(sealed, "org-1", "connector/jira/api_token", 1), ring) == "s3cr3t-value"
    other = seal("s3cr3t-value", ring, "org-1", "connector/jira/api_token", 1)
    assert other.ciphertext != sealed.ciphertext, "a fresh data key and nonce at every seal"


@pytest.mark.parametrize(
    ("org", "name", "version"),
    [
        ("org-2", "connector/jira/api_token", 1),
        ("org-1", "connector/other/api_token", 1),
        ("org-1", "connector/jira/api_token", 2),
    ],
    ids=["another org", "another name", "another version"],
)
def test_a_row_moved_elsewhere_does_not_open(org: str, name: str, version: int) -> None:
    """The additional authenticated data binds a ciphertext to its org, name and version."""
    ring = _ring("k1")
    sealed = seal("v4lue-secret", ring, "org-1", "connector/jira/api_token", 1)
    with pytest.raises(SecretStoreUnavailable):
        unseal(_row(sealed, org, name, version), ring)


def test_a_tampered_ciphertext_and_an_unknown_key_are_refused() -> None:
    ring = _ring("k1")
    sealed = seal("v4lue-secret", ring, "org-1", "connector/a/b", 1)
    row = _row(sealed, "org-1", "connector/a/b", 1)
    row.ciphertext = bytes([row.ciphertext[0] ^ 1]) + row.ciphertext[1:]
    with pytest.raises(SecretStoreUnavailable, match="does not open"):
        unseal(row, ring)
    other = _row(sealed, "org-1", "connector/a/b", 1)
    with pytest.raises(SecretStoreUnavailable, match="no longer lists"):
        unseal(other, _ring("k9"))


def test_the_keyring_names_what_is_wrong() -> None:
    assert parse_keyring("") is None and parse_keyring(None) is None
    ring = _ring("k2", "k1")
    assert ring.active_id == "k2" and set(ring.keys) == {"k1", "k2"}
    for raw, motif in (
        ("no-colon", "id:base64"),
        ("k1:not base64!!", "base64"),
        (f"k1:{base64.b64encode(b'short').decode()}", "32 bytes"),
        (f"k1:{_key()},k1:{_key()}", "twice"),
    ):
        with pytest.raises(SecretStoreUnavailable, match=motif):
            parse_keyring(raw)


async def test_a_value_is_stored_sealed_replaced_and_read(app: Any, org: str) -> None:
    from choregos_api.db.models import Organization, OrgSecret
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    store = SealedDbSecretStore(_ring("k1"))
    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        first = await store.put(session, org_id, "connector/jira/api_token", "first-value", "a@b.c")
        second = await store.put(session, org_id, "connector/jira/api_token", "second-value", "d@e.f")
    assert (first.version, second.version) == (1, 2)
    async with session_scope() as session:
        assert await store.get_many(session, org_id, ["connector/jira/api_token"]) == {
            "connector/jira/api_token": "second-value"
        }
        row = (await session.execute(select(OrgSecret))).scalar_one()
        assert row.version == 2 and row.updated_by == "d@e.f" and row.created_by == "a@b.c"
        assert b"second-value" not in row.ciphertext
        with pytest.raises(SecretIntrouvable, match="no value is stored"):
            await store.get_many(session, org_id, ["connector/jira/missing"])
        assert await store.delete(session, org_id, "connector/jira/api_token") is True
        assert await store.delete(session, org_id, "connector/jira/api_token") is False


async def test_a_rewrap_moves_the_data_keys_to_the_active_key_and_keeps_the_values(
    app: Any, org: str
) -> None:
    from choregos_api.db.models import Organization, OrgSecret
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    old = _ring("k1")
    new = parse_keyring(f"k2:{_key()},k1:{base64.b64encode(old.keys['k1']).decode()}")
    assert new is not None
    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        await SealedDbSecretStore(old).put(session, org_id, "connector/a/b", "keep-this-value", None)
    async with session_scope() as session:
        before = (await session.execute(select(OrgSecret))).scalar_one()
        ciphertext = before.ciphertext
        assert before.kek_id == "k1"
        assert await SealedDbSecretStore(new).rewrap(session) == 1
        assert await SealedDbSecretStore(new).rewrap(session) == 0, "idempotent"
    async with session_scope() as session:
        row = (await session.execute(select(OrgSecret))).scalar_one()
        assert row.kek_id == "k2" and row.ciphertext == ciphertext, "only the wrapping changed"
        assert await SealedDbSecretStore(new).get_many(session, org_id, ["connector/a/b"]) == {
            "connector/a/b": "keep-this-value"
        }
        # the old key alone no longer opens it: k2 is what it was re-wrapped under
        with pytest.raises(SecretIntrouvable, match="no longer lists"):
            await SealedDbSecretStore(old).get_many(session, org_id, ["connector/a/b"])


async def test_without_a_key_a_write_is_refused_and_a_read_says_why(
    app: Any, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.session import session_scope

    monkeypatch.delenv(ENV_KEKS, raising=False)
    store = SealedDbSecretStore()
    assert not store.available()
    async with session_scope() as session:
        with pytest.raises(SecretStoreUnavailable, match=ENV_KEKS):
            await store.put(session, "org-1", "connector/a/b", "value-123", None)
        with pytest.raises(SecretIntrouvable, match=ENV_KEKS):
            await store.get_many(session, "org-1", ["connector/a/b"])


def test_the_default_store_is_the_fake_only_under_fakes_without_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_KEKS, raising=False)
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    assert isinstance(get_secret_store(), FakeSecretStore)
    monkeypatch.setenv(ENV_KEKS, f"k1:{_key()}")
    assert isinstance(get_secret_store(), SealedDbSecretStore), "a configured key always wins"
    monkeypatch.delenv(ENV_KEKS)
    monkeypatch.delenv("CHOREGOS_FAKES")
    assert isinstance(get_secret_store(), SealedDbSecretStore) and not get_secret_store().available()
    scripted = FakeSecretStore(available=False)
    set_secret_store(scripted)
    try:
        assert get_secret_store() is scripted
    finally:
        set_secret_store(None)


async def test_resolved_configuration_reads_store_references_and_remembers_the_values() -> None:
    """The sync core cannot read `store:`; the platform's resolver does, and what it read is masked
    from then on."""
    forget_secrets()
    store = FakeSecretStore()
    await store.put(None, "org-1", "connector/jira/api_token", "tok-from-the-store", "x")  # type: ignore[arg-type]
    config = await resolved_configuration(
        None,  # type: ignore[arg-type]
        "org-1",
        "tracker",
        "jira",
        {"base_url": "https://acme.atlassian.net"},
        {"api_token": "store:connector/jira/api_token"},
        store=store,
    )
    assert config["api_token"] == "tok-from-the-store" and config["base_url"].startswith("https")
    assert redact("the server said tok-from-the-store is wrong") == "the server said [REDACTED] is wrong"
    forget_secrets()
