# SPDX-License-Identifier: Apache-2.0
"""`AgentTrial`: one run of a draft agent version on a sample input, before it is published (S32-03).

It reuses the run machinery — `prepare_stage`, `start_run`, `await_run`, `collect_spend`,
`record_run_outcome` — and nothing of the interpreter: no transition, no gate, no tracker, no SCM.
The work item it runs on is a trial (`state = "trial"`), created by the API and left out of the
board. The draft stands in for a registry agent (`StagePlan.trial_spec`), and the budget the API
capped mints the gateway key (`StagePlan.trial_budget_usd`): a trial cannot spend more.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow

from .interpreter import DEFAULT_RETRY, POLL_RETRY
from .planification import executer_activite

with workflow.unsafe.imports_passed_through():
    from ..activities import stage as stage_activities

#: The transition a trial's run bears, in its id and in the journal (`choregos_api.temporal.trial_run_id`).
TRIAL_TRANSITION = "trial"


@workflow.defn(name="AgentTrial", sandboxed=False)
class AgentTrial:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        project_id = payload["project_id"]
        work_item_id = payload["work_item_id"]
        prepared = await executer_activite(
            stage_activities.prepare_stage,
            {
                "project_id": project_id,
                "work_item_id": work_item_id,
                "transition_id": TRIAL_TRANSITION,
                "role": payload.get("role") or "implement",
                "from_state": "trial",
                "to_state": "trial",
                "actor": "trial",
                "attempt": 1,
                "trial_spec": payload["spec"],
                "trial_budget_usd": float(payload["budget_usd"]),
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        run_id = prepared["run_id"]
        budget = prepared["stage_input"]["budget"]
        await executer_activite(
            stage_activities.start_run,
            {"project_id": project_id, "work_item_id": work_item_id, "stage_input": prepared["stage_input"]},
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        awaited = await executer_activite(
            stage_activities.await_run,
            {"project_id": project_id, "run_id": run_id, "timeout_minutes": int(budget["max_minutes"]) + 10},
            start_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 20),
            schedule_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 60),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=POLL_RETRY,
        )
        spend = await executer_activite(
            stage_activities.collect_spend,
            {"project_id": project_id, "run_id": run_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        await executer_activite(
            stage_activities.record_run_outcome,
            {"project_id": project_id, "run_id": run_id, "result": awaited["result"]},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        resultat = awaited["result"] or {}
        return {
            "run_id": run_id,
            "status": resultat.get("status"),
            "summary": resultat.get("summary"),
            "cost_usd": float(spend.get("cost_usd", 0.0)),
            "budget_usd": float(budget["usd"]),
        }
