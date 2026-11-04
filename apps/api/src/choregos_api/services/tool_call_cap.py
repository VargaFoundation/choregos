# SPDX-License-Identifier: Apache-2.0
"""A run's tool-call cap holds when its calls arrive at once (S57-03, ADR 0122 §7).

`budgets.tool_calls_per_run` is counted on the cost ledger: the run's `kind="tool"` lines. The route
used to count them, call the tool, and book its line at the end: two calls of the same run made at
once each read the same count, and both passed. With the tool bridge, a script runs several calls
of a run at once, so eight calls at `cap - 1` let eight through.

The count is now taken under a per-run transaction lock, and the call takes its place right there:
a provisional `tool` line, committed before the call leaves, so the next call counts it. The lock
lives for the count and that insert only, never for the call itself: a connector may take thirty
seconds to answer, and the run's other calls would wait behind it.

When the call ends, the provisional line is deleted in the request's own transaction, beside the
line the call books — or does not book: a call the network policy stopped is still not counted
(ADR 0070). A call that fails gives its place back. A request whose final commit fails keeps its
place: the cap then errs by one call fewer, never one more.
"""

from __future__ import annotations

from dataclasses import dataclass

from choregos_core import utcnow
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import CostLedger, Run
from ..logging import get_logger
from .budgets import lock_budget_scopes

log = get_logger("choregos.tool_call_cap")

#: The provider of a provisional line: the call holds its place under the cap and has not answered.
IN_FLIGHT = "in_flight"


def lock_scope(run_id: str) -> str:
    """The budget scope a run's tool calls are counted under (`lock_budget_scopes`)."""
    return f"tool_calls:{run_id}"


@dataclass(frozen=True)
class ToolCallSlot:
    """A call's place under its run's tool-call cap."""

    #: The run's tool calls before this one: booked, or still in flight.
    already_called: int
    #: The provisional line that holds this call's place. None when the run has no cap, or when the
    #: cap is reached (nothing was taken: the route answers 429).
    line_id: str | None = None
    #: The RLS scope of the request (`app.current_orgs`), set again after each commit made here.
    scope: str | None = None

    async def settle(self, session: AsyncSession) -> None:
        """The call answered: its provisional line goes, in the request's transaction, which also
        holds the line the call books (if it books one). Both are kept or neither."""
        if self.line_id is not None:
            await session.execute(delete(CostLedger).where(CostLedger.id == self.line_id))

    async def give_back(self, session: AsyncSession) -> None:
        """The call failed: the request's writes go, as its rollback would have dropped them, and its
        place goes with them, committed at once. Never raises: the call's own error is the answer."""
        if self.line_id is None:
            return
        try:
            await session.rollback()
            await _set_scope(session, self.scope)
            await session.execute(delete(CostLedger).where(CostLedger.id == self.line_id))
            await session.commit()
        except Exception as error:
            # The place stays taken: one call fewer for the run, never one more.
            log.warning("tool_call_cap.give_back_failed", line=self.line_id, error=type(error).__name__)


async def take_tool_call_slot(session: AsyncSession, run: Run, tool: str, cap: int) -> ToolCallSlot:
    """Counts the run's tool calls under the run's lock and, below the cap, takes this call's place.

    Without a cap there is nothing to count and nothing is locked. Under the cap, the provisional
    line is COMMITTED here, in the request's session: the lock ends with that transaction, and the
    line is visible to the next call before this one leaves. The request has written nothing before
    it counts, so this commit carries the provisional line alone; the request's scope is set again
    right after, as the broker does for a governed write.

    PostgreSQL: `pg_advisory_xact_lock` on the run. SQLite: the in-process lock `lock_budget_scopes`
    stands in for it — SQLite reads outside the writer's transaction, so a count and an insert made
    apart are not serialised by its write lock.
    """
    if not cap:
        return ToolCallSlot(already_called=0)
    await lock_budget_scopes(session, [lock_scope(run.id)])
    already_called = (
        await session.execute(
            select(func.count())
            .select_from(CostLedger)
            .where(CostLedger.run_id == run.id, CostLedger.kind == "tool")
        )
    ).scalar_one()
    if already_called >= cap:
        return ToolCallSlot(already_called=already_called)
    scope = await _scope(session)
    line = CostLedger(
        project_id=run.project_id,
        work_item_id=run.work_item_id,
        run_id=run.id,
        ts=utcnow(),
        kind="tool",
        provider=IN_FLIGHT,
        stage_role=run.stage_role,
        model=tool,
    )
    session.add(line)
    await session.commit()
    await _set_scope(session, scope)
    return ToolCallSlot(already_called=already_called, line_id=line.id, scope=scope)


async def _scope(session: AsyncSession) -> str | None:
    if get_settings().is_sqlite:
        return None
    value = (await session.execute(text("SELECT current_setting('app.current_orgs', true)"))).scalar_one()
    return str(value) if value is not None else None


async def _set_scope(session: AsyncSession, scope: str | None) -> None:
    """`set_config(..., true)` lives for one transaction (`limit_to_organisations`): set it again."""
    if get_settings().is_sqlite:
        return
    await session.execute(text("SELECT set_config('app.current_orgs', :orgs, true)"), {"orgs": scope or ""})


__all__ = ["IN_FLIGHT", "ToolCallSlot", "lock_scope", "take_tool_call_slot"]
