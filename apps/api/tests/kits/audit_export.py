"""What the SIEM export's tests share (S80-05b, ADR 0207): a chained log written without PostgreSQL's
trigger, and a receiver that verifies a stream of exported events with nothing of the platform's code."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from tranibor_api.audit_chain import GENESIS, Link, link_hash
from tranibor_api.db.models import AuditChainHead, AuditLog
from tranibor_api.db.session import session_scope

T0 = datetime(2026, 11, 20, 8, 0, 0, 250000, tzinfo=UTC)


async def write_chain(org_id: str | None, count: int, *, start: int = 1, prev: str | None = None) -> str:
    """Entries `start` to `start + count - 1` of one chain, linked as the trigger links them, and the chain's
    head moved to the last. `prev` is the hash of entry `start - 1` (`GENESIS` from 1). Returns it."""
    async with session_scope(orgs="*") as session:
        before = prev if prev is not None else GENESIS
        for seq in range(start, start + count):
            payload: dict[str, Any] = {"n": seq, "note": "é", "ratio": 0.1}
            link = Link(
                # A row id is 36 characters at most (`PkMixin`): a name-based UUID, the same on every run.
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"audit/{org_id or 'platform'}/{seq}")),
                org_id=org_id,
                seq=seq,
                ts=T0 + timedelta(seconds=seq),
                actor_kind="user",
                actor_id="u-1",
                action=f"thing.{seq}",
                target_type="thing",
                target_id=str(seq),
                payload=json.dumps(payload),
                prev_hash=before,
                hash=None,
            )
            digest = link_hash(before, link)
            session.add(
                AuditLog(
                    id=link.id,
                    org_id=org_id,
                    seq=seq,
                    ts=link.ts,
                    actor_kind="user",
                    actor_id="u-1",
                    action=link.action,
                    target_type="thing",
                    target_id=str(seq),
                    payload=payload,
                    prev_hash=before,
                    hash=digest,
                )
            )
            before = digest
        chain = org_id or ""
        head = await session.get(AuditChainHead, chain)
        if head is None:
            session.add(AuditChainHead(chain=chain, org_id=org_id, seq=start + count - 1, hash=before))
        else:
            head.seq, head.hash = start + count - 1, before
        return before


def check_stream(events: list[dict[str, Any]]) -> dict[str, int]:
    """What a receiver does with exported events, from the JSON alone: per chain, each new event links to the
    one before and gives its hash from `canonical`; a repeat (the guarantee is at least once) must be the very
    entry already seen. Returns the places each chain has. A place skipped, or a different entry, fails."""
    seen: dict[str, dict[int, str]] = {}
    for event in events:
        chain = event["org_id"] or ""
        hashes = seen.setdefault(chain, {})
        seq = event["seq"]
        if seq in hashes:
            assert hashes[seq] == event["hash"], f"{chain}: entry {seq} came again, different"
            continue
        assert seq == len(hashes) + 1, f"{chain}: entry {len(hashes) + 1} is missing, {seq} came"
        previous = hashes[seq - 1] if seq > 1 else GENESIS
        assert event["prev_hash"] == previous
        assert hashlib.sha256((event["prev_hash"] + event["canonical"]).encode()).hexdigest() == event["hash"]
        hashes[seq] = event["hash"]
    return {chain: len(hashes) for chain, hashes in seen.items()}
