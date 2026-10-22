# SPDX-License-Identifier: Apache-2.0
"""`KnowledgeDistill`: a knowledge base distilled in passes (S42-10c, ADR 0082).

plan, then one `distill_part` per part (the budget is checked before each call), then
`reduce_distillation`, then `publish_distillation`. Every activity stores its result by digest, so a
retry or a replay makes no second model call. A refusal (too large, over budget, nothing cited) or
a failing part ends the run through `fail_distillation`: the base says why, nothing is published.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

from .planification import executer_activite

with workflow.unsafe.imports_passed_through():
    from ..activities import knowledge as activities

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=10))
TIMEOUT = timedelta(minutes=20)
EPSILON = 1e-9


def _code_of(error: BaseException) -> tuple[str, str]:
    cause = getattr(error, "cause", None) or error
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

    async def _call(self, activity: Any, payload: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await executer_activite(
            activity, payload, start_to_close_timeout=TIMEOUT, retry_policy=RETRY
        )
        return result

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
            for part in planned["parts"]:
                cost = part["est_usd"]
                if cost is not None and self.spent + float(cost) > budget + EPSILON:
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
