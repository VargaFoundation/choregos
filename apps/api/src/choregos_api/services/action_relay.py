# SPDX-License-Identifier: Apache-2.0
"""The relay of approved actions (ADR 0085): the `approved` row is the outbox of `ActionWorkflow`.

An approval commits, THEN its workflow is started (`start_once_committed`). Between the two, Temporal
may be down or the process killed: the action stays `approved` and nothing runs it — before this
relay, for ever (a replayed broker call finds the action and starts nothing). Every replica runs the
relay; starting `action-<id>` twice starts it once, so the replicas need no lock.

What it does not do: start an action still `pending_approval`, or restart one already `running` —
`load_action` moved it there, its workflow is Temporal's to retry.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from choregos_contracts import ActionStatus
from choregos_core import utcnow
from sqlalchemy import select

from ..db.models import Action
from ..db.session import TOUT, session_scope
from ..logging import get_logger

logger = get_logger("choregos.actions.relay")

#: An approval younger than this is left to the start that follows its own commit.
GRACE = timedelta(seconds=30)
INTERVAL_S = 30.0
BATCH = 100


async def start_approved_actions(*, grace: timedelta = GRACE, batch: int = BATCH) -> list[str]:
    """Start the workflow of every action approved more than `grace` ago and still `approved`.

    Returns the actions whose start Temporal accepted (or already had). One that fails stays
    `approved`, and the next pass tries again."""
    from ..temporal import action_id as workflow_id
    from ..temporal import get_temporal

    async with session_scope(orgs=TOUT) as session:
        stranded = list(
            (
                await session.execute(
                    select(Action.id)
                    .where(
                        Action.status == ActionStatus.APPROVED.value, Action.updated_at <= utcnow() - grace
                    )
                    .order_by(Action.updated_at)
                    .limit(batch)
                )
            ).scalars()
        )
    started: list[str] = []
    for action_id in stranded:
        try:
            await get_temporal().start_action(workflow_id(action_id), {"action_id": action_id})
        except Exception as error:
            logger.warning(
                "action.relay.failed", action=action_id, error=f"{type(error).__name__}: {error}"[:300]
            )
            continue
        started.append(action_id)
    if started:
        logger.info("action.relay.started", actions=started)
    return started


async def relay_loop(interval_s: float = INTERVAL_S) -> None:
    """Background task of the API: a failed pass does not stop the API, the next one tries again."""
    while True:
        try:
            await start_approved_actions()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("action.relay.pass_failed", error=str(error)[:200])
        await asyncio.sleep(interval_s)
