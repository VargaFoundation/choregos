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
    from choregos_api.db.models import KnowledgeSource
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.sync import sync_source

    result = await sync_source(str(payload["org"]), str(payload["source_id"]))
    if result.get("status") == "gone":
        return result
    # The interval the console set, read at each pass: changing it needs no signal.
    async with session_scope(orgs=[str(payload["org"])]) as session:
        source = await session.get(KnowledgeSource, str(payload["source_id"]))
        interval = source.interval_minutes * 60 if source is not None else None
    return {**result, "interval_seconds": interval}
