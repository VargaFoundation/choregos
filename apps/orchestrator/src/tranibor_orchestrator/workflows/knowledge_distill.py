# SPDX-License-Identifier: Apache-2.0
"""`KnowledgeDistill`: a knowledge base distilled in passes (S42-10c, ADR 0082).

plan, then one `distill_part` per part (the budget is checked before each call), then
`reduce_distillation`, then `publish_distillation`. Every activity stores its result by digest, so a
retry or a replay makes no second model call. A refusal (too large, over budget, nothing cited) or
a failing part ends the run through `fail_distillation`: the base says why, nothing is published.

Batch mode (S4-09, ADR 0190), only when the plan says `batch`: before the parts, the parts nobody has
stored go to the provider's batch API in one `submit_distillation_batch` (it keeps the batch id, so a
replay submits nothing), then `collect_distillation_batch` is polled between durable timers until the
batch has ended. The `distill_part` activities that follow find the stored answers and call the model
only for a part the batch could not answer. A plan without `batch` (every history recorded before
this) takes the unchanged path.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

from .scheduling import execute_on_its_queue

with workflow.unsafe.imports_passed_through():
    from ..activities import knowledge as activities

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=10))
TIMEOUT = timedelta(minutes=20)
EPSILON = 1e-9
#: Polling a batch rides out a provider's bad hour: many attempts, spaced, none counted as a failure
#: of the distillation until they are spent.
POLL_RETRY = RetryPolicy(
    maximum_attempts=8,
    initial_interval=timedelta(seconds=30),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=10),
)
#: A batch ends within 24 hours; the workflow stops waiting a little after that.
MAX_BATCH_WAIT = timedelta(hours=26)


def _code_of(error: BaseException) -> tuple[str, str]:
    cause = getattr(error, "cause", None) or error
    if isinstance(cause, CancelledError):  # the run was stopped (S42-10d): not a gateway failure
        return "stopped", "cancelled"
    if isinstance(cause, ApplicationError) and cause.type == "DistillationError":
        return str(cause.message), str(cause.details[0]) if cause.details else "gateway"
    return str(cause), "gateway"


@workflow.defn(name="KnowledgeDistill", sandboxed=False)
class KnowledgeDistill:
    def __init__(self) -> None:
        self.phase = "plan"
        self.parts_done = 0
        self.spent = 0.0

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {"phase": self.phase, "parts_done": self.parts_done, "spent_usd": self.spent}

    async def _call(
        self, activity: Any, payload: dict[str, Any], retry: RetryPolicy = RETRY
    ) -> dict[str, Any]:
        result: dict[str, Any] = await execute_on_its_queue(
            activity, payload, start_to_close_timeout=TIMEOUT, retry_policy=retry
        )
        return result

    async def _map_in_batch(self, base: dict[str, Any], planned: dict[str, Any]) -> set[str]:
        """Submits, then polls on timers until the batch ends; returns the parts it stored."""
        sent = await self._call(activities.submit_distillation_batch, base)
        if sent.get("status") != "submitted":  # nothing to do, or no batch: the synchronous path
            return set()
        pause = timedelta(seconds=max(int(planned.get("batch_poll_s") or 120), 1))
        waited = timedelta()
        while waited <= MAX_BATCH_WAIT:
            got = await self._call(activities.collect_distillation_batch, base, POLL_RETRY)
            if got.get("status") == "done":
                self.spent += float(got["cost_usd"])
                return {str(d) for d in got["digests"]}
            await workflow.sleep(pause)
            waited += pause
        raise ApplicationError(
            "the batch did not end within 26 hours", "gateway", type="DistillationError", non_retryable=True
        )

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        base = {"org": payload["org"], "base_id": payload["base_id"], "exclude": payload.get("exclude") or []}
        try:
            planned = await self._call(
                activities.plan_distillation, {**base, "max_usd": payload.get("max_usd")}
            )
            if planned.get("status") == "gone":
                return {"status": "gone"}
            budget = float(planned["budget_usd"])
            digests: list[str] = []
            self.phase = "map"
            batched = await self._map_in_batch(base, planned) if planned.get("batch") else set()
            for part in planned["parts"]:
                cost = part["est_usd"]
                # a part the batch stored costs nothing more: its price is already in `spent`
                if (
                    part["digest"] not in batched
                    and cost is not None
                    and self.spent + float(cost) > budget + EPSILON
                ):
                    raise ApplicationError(
                        f"stopped before part {part['index'] + 1}: ${self.spent:.2f} spent, the next "
                        f"call would pass the budget of ${budget:.2f}; nothing was published",
                        "over_budget",
                        type="DistillationError",
                        non_retryable=True,
                    )
                done = await self._call(
                    activities.distill_part,
                    {**base, "index": part["index"], "digest": part["digest"], "spent_usd": self.spent},
                )
                self.spent += float(done["cost_usd"])
                self.parts_done += 1
                digests.append(part["digest"])
            self.phase = "reduce"
            merged = await self._call(
                activities.reduce_distillation, {**base, "digests": digests, "spent_usd": self.spent}
            )
            self.spent += float(merged["cost_usd"])
            self.phase = "publish"
            return await self._call(
                activities.publish_distillation,
                {
                    **base,
                    "digest": merged["digest"],
                    "requested_by": payload["requested_by"],
                    "spent_usd": self.spent,
                    "parts": len(digests),
                },
            )
        except asyncio.CancelledError:
            # A person stopped the run (S42-10d, ADR 0092). The model call in flight finishes and is
            # booked by its own activity; nothing more is called and nothing is published. The base's
            # record is already `stopped` (the API wrote it before cancelling), `fail_distillation`
            # completes it. Only this branch is new: a history without a cancellation replays unchanged.
            return await self._call(
                activities.fail_distillation,
                {
                    "org": payload["org"],
                    "base_id": payload["base_id"],
                    "requested_by": payload["requested_by"],
                    "code": "cancelled",
                    "error": "stopped",
                    "spent_usd": self.spent,
                },
            )
        except (ActivityError, ApplicationError) as error:
            message, code = _code_of(error)
            return await self._call(
                activities.fail_distillation,
                {
                    "org": payload["org"],
                    "base_id": payload["base_id"],
                    "requested_by": payload["requested_by"],
                    "code": code,
                    "error": message,
                    "spent_usd": self.spent,
                },
            )
