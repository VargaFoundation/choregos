# SPDX-License-Identifier: Apache-2.0
"""Cancelling the conversation turns in flight of an assistant or of an agent (ADR 0142 §4, §4 bis).

A turn runs in the API process that took it, and an organisation may run several replicas. A stop
therefore cancels a turn twice over:

- it writes the cancel request on the conversation's row (`agent_sessions.turn_cancel_requested_at`,
  S63-23), which the turn reads between its model and tool calls (`conversation_turns.cancel_if_requested`)
  and which a replica on PostgreSQL also polls (`watch_cancel`), whichever replica runs it;
- it cancels the turn at once when it runs in this process (`cancel_turn`, the in-process registry).

A cancelled turn settles as a person's cancel does: its spend booked once, its key revoked, the
conversation left open. The conversations are read and flagged in the platform scope: they live in their
people's personal spaces, which the stopping person cannot read (ADR 0134). Only their ids are read,
never their content. The request is written and COMMITTED before any local cancel: the turn that settles
writes the same row, and must not wait for the stop's transaction.
"""

from __future__ import annotations

from choregos_core import utcnow
from sqlalchemy import select, update

from ..db.models import AgentSessionRecord
from ..db.session import session_scope
from ..logging import get_logger
from .conversation_turns import cancel_turn

logger = get_logger("choregos.turn_cancels")

#: How long a stop waits for a turn of this process to settle (its spend booked, its key revoked).
LOCAL_WAIT_S = 10.0


async def cancel_turns(
    org_id: str, *, installation_id: str | None = None, agent_slug: str | None = None
) -> list[str]:
    """Asks every turn in flight of the organisation's conversations of `installation_id` (or of the agent
    `agent_slug`) to stop, in every person's space, and cancels at once those running in this process.
    Returns the conversations whose turn was asked to stop.

    Once the request is written, the turn reads it at its next model or tool call whatever happens here:
    a local cancel that fails is logged, and the turn is still counted."""
    if (installation_id is None) == (agent_slug is None):
        raise ValueError("cancel the turns of one installation or of one agent")
    ids = await _request(org_id, installation_id=installation_id, agent_slug=agent_slug)
    for conversation_id in ids:
        try:
            await cancel_turn(conversation_id, wait_s=LOCAL_WAIT_S)
        except Exception as failure:  # the request is written: the turn still reads it at its next call
            logger.warning(
                "turn_cancels.local_cancel_failed", session_id=conversation_id, error=type(failure).__name__
            )
    return ids


async def _request(org_id: str, *, installation_id: str | None, agent_slug: str | None) -> list[str]:
    """The conversations with a turn in flight (a live lease), flagged, committed; their ids."""
    now = utcnow()
    in_flight = [
        AgentSessionRecord.org_id == org_id,
        AgentSessionRecord.kind == "conversation",
        AgentSessionRecord.turn_lease_until > now,
    ]
    if installation_id is not None:
        in_flight.append(AgentSessionRecord.installation_id == installation_id)
    else:
        in_flight.append(AgentSessionRecord.agent_slug == agent_slug)
    async with session_scope(orgs="*") as platform:  # every person's space: ids only (ADR 0142 §4 bis)
        ids = list(
            (
                await platform.execute(
                    select(AgentSessionRecord.id).where(*in_flight).order_by(AgentSessionRecord.id)
                )
            ).scalars()
        )
        if ids:
            await platform.execute(
                update(AgentSessionRecord)
                .where(AgentSessionRecord.id.in_(ids), AgentSessionRecord.turn_lease_until > now)
                .values(turn_cancel_requested_at=now)
            )
    return ids


__all__ = ["LOCAL_WAIT_S", "cancel_turns"]
