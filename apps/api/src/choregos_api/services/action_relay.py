# SPDX-License-Identifier: Apache-2.0
"""The relay of approved actions (ADR 0085): the `approved` row is the outbox of `ActionWorkflow`.

An approval commits, THEN its workflow is started (`start_once_committed`). Between the two, Temporal
may be down or the process killed: the action stays `approved` and nothing runs it — before this
relay, for ever (a replayed broker call finds the action and starts nothing). Every replica runs the
relay; starting `action-<id>` twice starts it once, so the replicas need no lock.

What it does not do: start an action still `pending_approval`, or restart one already `running` —
`load_action` moved it there, its workflow is Temporal's to retry. An action a person settled after a
doubt (S65-26) is `approved` again although its first run completed: that one is restarted under the
same id, the only start that reuses it.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from choregos_contracts import ActionStatus
from choregos_core import utcnow
from sqlalchemy import select

from ..db.models import Action
from ..db.session import ALL_ORGS, session_scope
from ..logging import get_logger

logger = get_logger("choregos.actions.relay")

#: An approval younger than this is left to the start that follows its own commit.
GRACE = timedelta(seconds=30)
BATCH = 100


async def start_approved_actions(*, grace: timedelta = GRACE, batch: int = BATCH) -> list[str]:
    """Start the workflow of every action approved more than `grace` ago and still `approved`.

    Returns the actions whose start Temporal accepted (or already had). One that fails stays
    `approved`, and the next pass tries again."""
    from ..temporal import action_id as workflow_id
    from ..temporal import get_temporal

    async with session_scope(orgs=ALL_ORGS) as session:
        stranded = list(
            (
                await session.execute(
                    select(Action.id, Action.result)
                    .where(
                        Action.status == ActionStatus.APPROVED.value, Action.updated_at <= utcnow() - grace
                    )
                    .order_by(Action.updated_at)
                    .limit(batch)
                )
            ).all()
        )
    started: list[str] = []
    for action_id, result in stranded:
        gateway = get_temporal()
        # An action that was closed in doubt and settled since (S65-26): its first run completed, and
        # only a restart reuses the id. Any other approved action is started, never restarted.
        settled = isinstance(result, dict) and bool(result.get("in_doubt"))
        begin = gateway.restart_action if settled else gateway.start_action
        try:
            await begin(workflow_id(action_id), {"action_id": action_id})
        except Exception as error:
            logger.warning(
                "action.relay.failed", action=action_id, error=f"{type(error).__name__}: {error}"[:300]
            )
            continue
        started.append(action_id)
    if started:
        logger.info("action.relay.started", actions=started)
    return started


async def relay_loop(interval_s: float | None = None) -> None:
    """Background task of the API: a failed pass does not stop the API, the next one tries again."""
    from ..config import get_settings

    if interval_s is None:
        interval_s = get_settings().action_relay_interval_s
    while True:
        try:
            await start_approved_actions()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("action.relay.pass_failed", error=str(error)[:200])
        await asyncio.sleep(interval_s)
