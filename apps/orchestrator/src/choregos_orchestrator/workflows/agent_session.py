# SPDX-License-Identifier: Apache-2.0
"""`AgentSessionPod`: the pod of a person's session with a hosted agent (S41-11, ADR 0080 §10).

Started by the session's first response, one per session (its id is `agent-session-{id}`: a second
start is refused by Temporal, so a second pod never starts). It asks for the pod, then waits: each
response signals `touch`, which restarts the idle wait; closing the session signals `close`. The pod
stops at the first of: a `close`, `idle_s` without a response, the session's expiry. A pod that
cannot start closes the session `pod_failed`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .planification import executer_activite

with workflow.unsafe.imports_passed_through():
    from ..activities import agent_session as session_activities

START_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=[session_activities.POD_REFUSED],
)
STOP_RETRY = RetryPolicy(initial_interval=timedelta(seconds=5), maximum_attempts=10)


@workflow.defn(name="AgentSessionPod", sandboxed=False)
class AgentSessionPod:
    def __init__(self) -> None:
        self._touches = 0
        self._close: str | None = None

    @workflow.signal
    def touch(self, _: dict[str, Any] | None = None) -> None:
        self._touches += 1

    @workflow.signal
    def close(self, payload: dict[str, Any] | None = None) -> None:
        self._close = str((payload or {}).get("reason") or "closed")

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        session = {"session_id": payload["session_id"]}
        idle = timedelta(seconds=float(payload["idle_s"]))
        expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        try:
            await executer_activite(
                session_activities.start_session_pod,
                session,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=START_RETRY,
            )
        except ActivityError:
            return await self._stop(session, "pod_failed")
        reason = "idle"
        while self._close is None:
            seen = self._touches
            left = expires_at - workflow.now()
            if left <= timedelta(0):
                reason = "expired"
                break
            try:
                await workflow.wait_condition(
                    lambda seen=seen: self._close is not None or self._touches != seen,  # type: ignore[misc]
                    timeout=min(idle, left),
                )
            except TimeoutError:
                reason = "expired" if left < idle else "idle"
                break
        return await self._stop(session, self._close or reason)

    async def _stop(self, session: dict[str, Any], reason: str) -> dict[str, Any]:
        stopped = await executer_activite(
            session_activities.stop_session_pod,
            {**session, "reason": reason},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=STOP_RETRY,
        )
        return {**dict(stopped), "reason": reason}
