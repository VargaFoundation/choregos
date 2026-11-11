# SPDX-License-Identifier: Apache-2.0
"""Retention applied by code (S63-16, ADR 0133 point 6): the daily purge of an assistant's data.

The platform's periodic jobs are background tasks of the API (`relay_loop`, started in the lifespan, run by
every replica; the work is idempotent, so no lock). `retention_loop` is one more: each pass deletes

- the conversations past `retention_until` (set at each turn from the installation's policy) with their
  transcript events, through the same `delete_conversation` a person's deletion uses (the ledger lines stay);
- the memory facts of a `user`, `user_subject` or `installation` store older than the store's
  `retention_days`, pinned ones excepted as in `assistant_memory._expire`;
- the old versions of a profile past the newest ten (the live ones stay), `purge_profile_versions`.

`PURGERS` is where later stories register what else expires (task artifacts, S63-13): each is
`(session, now) -> rows deleted`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from choregos_core import utcnow
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AgentSessionRecord, AssistantInstallation, MemoryFact, MemoryProfile, MemoryStore
from ..db.session import ALL_ORGS, session_scope
from ..logging import get_logger
from .conversations import delete_conversation

log = get_logger("choregos.assistants.retention")

BATCH = 500

Purger = Callable[[AsyncSession, datetime], Awaitable[int]]
#: What else expires, by name. Task artifacts register here (`task_erasure`, S63-16).
PURGERS: dict[str, Purger] = {}


async def purge_conversations(session: AsyncSession, now: datetime, *, batch: int = BATCH) -> int:
    """Deletes the conversations whose `retention_until` has passed, with their events."""
    rows = list(
        (
            await session.execute(
                select(AgentSessionRecord)
                .where(
                    AgentSessionRecord.kind == "conversation",
                    AgentSessionRecord.retention_until.is_not(None),
                    AgentSessionRecord.retention_until <= now,
                )
                .order_by(AgentSessionRecord.retention_until)
                .limit(batch)
            )
        ).scalars()
    )
    for row in rows:
        await delete_conversation(session, row)
    return len(rows)


async def purge_memory(session: AsyncSession, now: datetime) -> int:
    """Deletes the unpinned facts older than the `retention_days` of their store."""
    stores = (
        await session.execute(
            select(MemoryStore).where(
                MemoryStore.installation_id.is_not(None), MemoryStore.retention_days.is_not(None)
            )
        )
    ).scalars()
    deleted = 0
    for store in list(stores):
        horizon = now - timedelta(days=int(store.retention_days or 0))
        facts = await session.execute(
            select(MemoryFact).where(MemoryFact.memory_store_id == store.id, MemoryFact.created_at < horizon)
        )
        old = [f.id for f in facts.scalars() if not (f.provenance or {}).get("pinned")]
        if old:
            await session.execute(delete(MemoryFact).where(MemoryFact.id.in_(old)))
            deleted += len(old)
    return deleted


#: How many versions of a profile a store keeps besides the live ones: `policy_overrides.retention.
#: profile_versions` of the installation, else this.
PROFILE_VERSIONS_KEPT = 10
LIVE = ("active", "proposed")


async def purge_profile_versions(session: AsyncSession, now: datetime) -> int:
    """Deletes the old versions of the profiles of an installation's stores: the newest
    `PROFILE_VERSIONS_KEPT`
    stay, and so does every `active` or `proposed` one (the live document and what waits for the person)."""
    deleted = 0
    for installation in (await session.execute(select(AssistantInstallation))).scalars().all():
        declared = ((installation.policy_overrides or {}).get("retention") or {}).get("profile_versions")
        keep = (
            declared
            if isinstance(declared, int) and not isinstance(declared, bool) and declared >= 1
            else (PROFILE_VERSIONS_KEPT)
        )
        stores = (
            await session.execute(
                select(MemoryStore.id).where(MemoryStore.installation_id == installation.id)
            )
        ).scalars()
        for store_id in list(stores):
            versions = list(
                (
                    await session.execute(
                        select(MemoryProfile)
                        .where(MemoryProfile.memory_store_id == store_id)
                        .order_by(MemoryProfile.version.desc())
                    )
                ).scalars()
            )
            old = [v.id for v in versions[keep:] if v.status not in LIVE]
            if old:
                await session.execute(delete(MemoryProfile).where(MemoryProfile.id.in_(old)))
                deleted += len(old)
    return deleted


async def purge_once(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    """One pass, in the platform scope of `session`; what each step deleted."""
    from . import task_erasure  # noqa: F401 - registers the purge of task artifacts

    moment = now or utcnow()
    done = {
        "conversations": await purge_conversations(session, moment),
        "memory_facts": await purge_memory(session, moment),
        "profile_versions": await purge_profile_versions(session, moment),
    }
    for name, purger in PURGERS.items():
        done[name] = await purger(session, moment)
    return done


async def run_purge() -> dict[str, int]:
    async with session_scope(orgs=ALL_ORGS) as session:
        return await purge_once(session)


async def retention_loop(interval_s: float) -> None:
    """Background task of the API: a failed pass is logged, the next one tries again."""
    while True:
        try:
            done = await run_purge()
            if any(done.values()):
                log.info("assistants.retention.purged", **done)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.warning("assistants.retention.pass_failed", error=str(error)[:200])
        await asyncio.sleep(interval_s)
