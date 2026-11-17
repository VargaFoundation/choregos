"""Entries and keys the audit chain's tests share (S80-05, ADR 0182): `test_audit_chain.py` walks them
without a database, `test_audit_chain_postgres.py` asks PostgreSQL's own function about the same entry."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from choregos_api.audit_chain import Link
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ORG = "0193a8f2-0000-7000-8000-000000000001"
T0 = datetime(2026, 11, 17, 23, 0, 0, 123456, tzinfo=UTC)


def new_key() -> str:
    """A fresh Ed25519 private key, PEM PKCS#8, as `openssl genpkey -algorithm ed25519` writes one."""
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def unlinked(seq: int, payload: str = "{}") -> Link:
    """Entry `seq` of `ORG`'s chain, not yet linked."""
    return Link(
        id=f"entry-{seq}",
        org_id=ORG,
        seq=seq,
        ts=T0 + timedelta(seconds=seq),
        actor_kind="user",
        actor_id="u-1",
        action="project.update",
        target_type="project",
        target_id="p-1",
        payload=payload,
        prev_hash=None,
        hash=None,
    )


#: An entry with what a canonical form can get wrong: a null, a tab, an accent, an escaped quote and a
#: newline, a number `jsonb` writes out in full.
TRICKY = replace(
    unlinked(7, '{"a": [1, 0.00000000015], "note": "é\\n\\"x\\""}'),
    actor_id=None,
    target_type=None,
    target_id="tab\there",
)
#: Its hash as the first entry of a chain, pinned: a change breaks every chain already written.
TRICKY_HASH = "9d520a274ff93296654886e126c2f6421b3bd2e9241ea401a30adab7be79bc7b"
