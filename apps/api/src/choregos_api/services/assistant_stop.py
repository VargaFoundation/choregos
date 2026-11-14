# SPDX-License-Identifier: Apache-2.0
"""Stop an assistant, and resume it (S64-09c, ADR 0142 §4 bis, §5-6).

The order is the point, as for an agent's stop (`agent_stop`). The installation is marked stopped (who,
when, why) and that is COMMITTED first: its derived status reads `suspended` from then on, so the next
turn of any of its conversations is refused (`conversation_installation.apply_installation`), whichever
replica takes it. Then every turn in flight of its conversations is asked to stop on the conversation's
row and cancelled at once when it runs here (`turn_cancels.cancel_turns`). The conversations are not
closed: they belong to their people (ADR 0134), who read them while the assistant is stopped and continue
them once it is resumed.

Stopping a stopped assistant keeps the first stop's who, when and why (the stop in force), cancels what
still runs and is audited again: the stop is idempotent, it never fails because it already happened.
Resuming clears the stop (409 when there is none); the status is derived again, and nothing stopped
restarts on its own.
"""

from __future__ import annotations

from choregos_core import utcnow
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import AssistantInstallation
from ..db.session import release_connection
from ..errors import conflict
from ..rbac import Principal
from .assistants import SUSPENDED, refresh
from .turn_cancels import cancel_turns


async def stop_assistant(
    session: AsyncSession, principal: Principal, row: AssistantInstallation, reason: str
) -> list[str]:
    """Marks `row` stopped, committed, then cancels its conversations' turns in flight; returns the
    conversations whose turn was asked to stop. The caller decided `assistant:manage`."""
    previous = row.status
    if row.stopped_at is None:
        row.stopped_at, row.stopped_by, row.stopped_reason = utcnow(), principal.email, reason
    row.status = SUSPENDED
    # Committed in the request's scope before any turn is touched: from here no new turn starts.
    await release_connection(session)
    cancelled = await cancel_turns(row.org_id, installation_id=row.id)
    await record(session, principal, "assistant.stop", org_id=row.org_id, target_type="assistant",
                 target_id=row.slug, reason=reason, before=previous,
                 turns_cancelled=len(cancelled))  # fmt: skip
    return cancelled


async def resume_assistant(session: AsyncSession, principal: Principal, row: AssistantInstallation) -> None:
    """Clears the stop; the status is derived again. 409 when the assistant is not stopped."""
    if row.stopped_at is None:
        raise conflict(f"`{row.slug}` is not stopped: there is nothing to resume")
    reason = row.stopped_reason
    row.stopped_at, row.stopped_by, row.stopped_reason = None, None, None
    status, _items = await refresh(session, row)
    await record(session, principal, "assistant.resume", org_id=row.org_id, target_type="assistant",
                 target_id=row.slug, stopped_reason=reason, status=status)  # fmt: skip


__all__ = ["resume_assistant", "stop_assistant"]
