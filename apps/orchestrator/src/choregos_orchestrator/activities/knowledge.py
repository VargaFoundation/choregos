# SPDX-License-Identifier: Apache-2.0
"""Syncing a knowledge source (S33-04, ADR 0051): the activity the `KnowledgeSync` loop runs.

Idempotent (ADR 0008): `sync_source` moves the source's cursor only once its changes are applied,
and applying them twice changes nothing twice. A source removed meanwhile answers `gone`, and the
loop stops.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity


@activity.defn(name="sync_knowledge_source")
async def sync_knowledge_source(payload: dict[str, Any]) -> dict[str, Any]:
    """One pass: sync when the source's row says it is due (S39-19), and answer how long to sleep.

    `not_due` touches nothing: a worker restart or `continue_as_new` does not sync off-schedule.
    """
    from choregos_api.db.models import KnowledgeSource
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge import refresh
    from choregos_api.knowledge.sync import sync_source

    org, source_id = str(payload["org"]), str(payload["source_id"])
    async with session_scope(orgs=[org]) as session:
        source = await session.get(KnowledgeSource, source_id)
        if source is None:
            return {"status": "gone"}
        decision = refresh.decide_pass(source, refresh.utcnow())
    if not decision.sync:
        return {"status": "not_due", "interval_seconds": decision.sleep_s}

    async def schedule_next() -> int:
        # Written on success and on failure alike, so the console's "next sync" stays true.
        now = refresh.utcnow()
        async with session_scope(orgs=[org]) as session:
            current = await session.get(KnowledgeSource, source_id)
            if current is None:
                return refresh.MIN_SLEEP_S
            current.next_sync_at = refresh.next_sync_after(current, now)
            return refresh.sleep_until(current.next_sync_at, now)

    try:
        result = await sync_source(org, source_id)
    except Exception:
        await schedule_next()
        raise
    if result.get("status") == "gone":
        return result
    return {**result, "interval_seconds": await schedule_next()}
