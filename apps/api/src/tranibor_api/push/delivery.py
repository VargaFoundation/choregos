# SPDX-License-Identifier: Apache-2.0
"""Who is told, and the telling (ADR 0194 §3-§6).

A call site says WHAT happened with `tell(session, Notice(...))`, inside the transaction that records
it. Nothing is read, nothing is sent there: the notice is queued and, once that transaction has
COMMITTED, a background task resolves the recipients and sends in a session of its own. So a push can
neither slow nor fail the transaction it announces, and a rollback sends nothing. Best effort by
design: a crash between the commit and the send loses the push, and the fact itself stays in its table.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tranibor_contracts import ActionStatus
from tranibor_core import utcnow

from ..config import get_settings
from ..db.models import Action, Membership, Organization, PushSubscription, User
from ..db.session import ALL_ORGS, after_commit, session_scope
from ..logging import get_logger
from . import sender as senders
from .sender import PushSender, Target

log = get_logger(__name__)

RANKS = {"viewer": 0, "developer": 1, "release_captain": 2, "project_owner": 3, "org_admin": 4}
#: At most this many devices are being called at once, whatever the number of recipients.
CONCURRENCY = 8
#: A device that has failed without a success for this long is forgotten at its next failure.
STALE_AFTER = timedelta(days=30)
#: The push service gone, or the device unregistered: the subscription is dead (RFC 8030 §7.3).
GONE = (404, 410)
#: Titles and lines are cut to the contract's bounds.
TITLE_MAX, BODY_MAX = 120, 200
URGENT = {"decision.awaiting"}


@dataclass(frozen=True)
class Notice:
    """One fact, and who may be told about it.

    `candidates`: the people the fact concerns (`None`: every member of the organisation). Of these,
    only those who hold at least `min_rank` on the project (or in the organisation) are told, never
    `exclude` (whoever did it)."""

    kind: str
    org_id: str
    title: str
    body: str
    ref_type: str
    ref_id: str
    #: A console path; `{org}` is replaced by the organisation's slug.
    path: str
    project_id: str | None = None
    candidates: frozenset[str] | None = None
    min_rank: int = 0
    exclude: frozenset[str] = field(default_factory=frozenset)
    #: The same, by e-mail (what a run's initiator and an action's proposer are named by).
    candidate_emails: frozenset[str] | None = None
    exclude_emails: frozenset[str] = field(default_factory=frozenset)
    #: A decision awaited: told only while the action still waits. A policy or a faster person may
    #: have settled it between the commit and the send, and "decision awaiting" would then be false.
    action_id: str | None = None


_tasks: set[asyncio.Task[int]] = set()


def tell(session: AsyncSession, notice: Notice) -> None:
    """Queue `notice` for after the commit. Never raises, never touches the database."""
    try:
        if not senders.configured(get_settings()):
            return
        after_commit(session, lambda: _spawn(notice))
    except Exception as error:  # a push must never fail what it announces
        log.warning("push.not_queued", kind=notice.kind, error=f"{type(error).__name__}: {error}"[:200])


async def _spawn(notice: Notice) -> None:
    task = asyncio.create_task(deliver(notice))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def wait_idle() -> None:
    """Until the pushes in flight are over: for the tests, and a graceful shutdown."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def recipients(session: AsyncSession, notice: Notice) -> set[str]:
    """The people of `notice` who hold the right to know: a membership of the organisation (or of
    the project) at `min_rank` at least. A person who left, or never had the right, is not told."""
    candidates = notice.candidates
    if notice.candidate_emails is not None:
        found = frozenset(
            (
                await session.execute(
                    select(User.id).where(
                        func.lower(User.email).in_({e.lower() for e in notice.candidate_emails})
                    )
                )
            ).scalars()
        )
        candidates = found if candidates is None else candidates & found
    excluded = set(notice.exclude)
    if notice.exclude_emails:
        excluded |= set(
            (
                await session.execute(
                    select(User.id).where(
                        func.lower(User.email).in_({e.lower() for e in notice.exclude_emails})
                    )
                )
            ).scalars()
        )
    query = select(Membership.user_id, Membership.role).where(Membership.org_id == notice.org_id)
    if notice.project_id is None:
        query = query.where(Membership.project_id.is_(None))
    else:
        query = query.where((Membership.project_id.is_(None)) | (Membership.project_id == notice.project_id))
    if candidates is not None:
        if not candidates:
            return set()
        query = query.where(Membership.user_id.in_(candidates))
    best: dict[str, int] = {}
    for user_id, role in (await session.execute(query)).all():
        best[user_id] = max(best.get(user_id, -1), RANKS.get(str(role), -1))
    return {u for u, rank in best.items() if rank >= notice.min_rank and u not in excluded}


def _wants(subscription: PushSubscription, kind: str) -> bool:
    return subscription.kinds is None or kind in subscription.kinds


async def deliver(notice: Notice, *, sender: PushSender | None = None) -> int:
    """Resolve, then send. Returns how many devices accepted it. Never raises."""
    try:
        sender = sender or senders.get_sender(get_settings())
        if sender is None:
            return 0
        async with session_scope(orgs=ALL_ORGS) as session:
            if notice.action_id is not None:
                waiting = await session.scalar(select(Action.status).where(Action.id == notice.action_id))
                if waiting != ActionStatus.PENDING_APPROVAL.value:
                    return 0
            people = await recipients(session, notice)
            if not people:
                return 0
            organisation = await session.get(Organization, notice.org_id)
            slug = organisation.slug if organisation is not None else ""
            rows = (
                (await session.execute(select(PushSubscription).where(PushSubscription.user_id.in_(people))))
                .scalars()
                .all()
            )
            targets = [
                (row.id, Target(row.endpoint, row.p256dh, row.auth))
                for row in rows
                if _wants(row, notice.kind)
            ]
        if not targets:
            return 0
        payload = payload_of(notice, slug)
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(identifier: str, target: Target) -> bool:
            async with gate:
                return await send_one(sender, identifier, target, payload, urgent=notice.kind in URGENT)

        return sum(await asyncio.gather(*(one(i, t) for i, t in targets)))
    except Exception as error:
        log.warning("push.delivery_failed", kind=notice.kind, error=f"{type(error).__name__}: {error}"[:300])
        return 0


def payload_of(notice: Notice, slug: str) -> dict[str, Any]:
    """The JSON the device receives: a title, a line, references and a path. Nothing else (ADR 0194 §2)."""
    return {
        "kind": notice.kind,
        "title": notice.title[:TITLE_MAX],
        "body": notice.body[:BODY_MAX],
        "org": slug,
        "ref": {"type": notice.ref_type, "id": notice.ref_id},
        "url": notice.path.replace("{org}", slug),
        "at": utcnow().isoformat(),
    }


async def attempt(
    sender: PushSender, target: Target, payload: dict[str, Any], *, urgent: bool = False
) -> int | None:
    """The push service's status, or `None` when it could not be reached or took too long."""
    try:
        return await asyncio.wait_for(
            sender.send(target, payload, urgency="high" if urgent else "normal"), timeout=15
        )
    except Exception as error:
        log.info("push.send_failed", error=type(error).__name__)
        return None


async def send_one(
    sender: PushSender, identifier: str, target: Target, payload: dict[str, Any], *, urgent: bool = False
) -> bool:
    """One device; True when the push service accepted it. The outcome is written down: a 404 or 410
    deletes the subscription, any other failure is counted and not retried."""
    status = await attempt(sender, target, payload, urgent=urgent)
    ok = status is not None and 200 <= status < 300
    try:
        await record_outcome(identifier, ok=ok, gone=status in GONE)
    except Exception as error:
        log.warning("push.outcome_not_recorded", subscription=identifier, error=type(error).__name__)
    return ok


async def record_outcome(identifier: str, *, ok: bool, gone: bool) -> None:
    now = utcnow()
    async with session_scope(orgs=ALL_ORGS) as session:
        if gone:
            await session.execute(delete(PushSubscription).where(PushSubscription.id == identifier))
            return
        row = await session.get(PushSubscription, identifier)
        if row is None:
            return
        if ok:
            row.last_delivered_at, row.failing_since = now, None
            return
        started = row.failing_since or now
        if now - _aware(started) > STALE_AFTER:
            await session.execute(delete(PushSubscription).where(PushSubscription.id == identifier))
            return
        await session.execute(
            update(PushSubscription)
            .where(PushSubscription.id == identifier)
            .values(last_failure_at=now, failing_since=started)
        )


def _aware(moment: Any) -> Any:
    from datetime import UTC

    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
