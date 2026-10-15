# SPDX-License-Identifier: Apache-2.0
"""Suspend a run's sandbox while a person answers its agent's question, then resume THAT run
(S35-03, ADR 0016, ADR 0053).

Without this, an answered question costs a second run: the stage replays from scratch, with a
new run, a new sandbox, a new clone, and the cost of everything the agent had already done.
With an executor that announces `suspend` and `resume` (`agent_sandbox`), the sandbox waits —
pod gone, workspace volume kept — and the same run goes on with the answer.

The wait is bounded by the policy's `sandbox.suspend_max_hours`, and the executor hands that
bound to the platform that runs the sandbox (the claim's `shutdownTime`): past it the sandbox
is deleted even if nothing here runs again, and the answer replays the stage as before.

Every activity here is idempotent (ADR 0008): replayed after a success, it does nothing twice.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_api.db.models import GatewayKeyRow, Run
from choregos_api.security import mint_run_token
from choregos_core import ExecRef, utcnow
from temporalio import activity

from ..config import get_settings
from .base import db, load_work_item, project_bundle, without_secrets
from .run_key import key_ttl_seconds, mint_key, revoke

#: What the agent reads first when its run goes on — the same heading as a replayed stage.
ANSWER_HEADING = "## A person answered the question that stopped this work item"


def _ref(run: Run, namespace: str) -> ExecRef:
    return ExecRef(
        kind=run.executor_kind or "fake",
        name=run.executor_ref or run.id,
        namespace=namespace,
        run_id=run.id,
    )


def _namespace(slug: str) -> str:
    return str(get_settings().runner_namespace_pattern).format(slug=slug)


def _can_suspend(executor: Any) -> bool:
    return {"suspend", "resume"} <= set(getattr(executor, "capabilities", frozenset()))


@activity.defn(name="suspend_run")
async def suspend_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Suspends the run's sandbox until the policy's bound. `suspended: False` when the
    executor cannot, the policy says 0, or there is nothing left to suspend."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, payload["run_id"])
        hours = float(bundle.policy.sandbox.suspend_max_hours)
        executor = bundle.adapters.executor
        if run is None or hours <= 0 or not _can_suspend(executor):
            return {"suspended": False, "max_hours": hours}
        until = utcnow() + timedelta(hours=hours)
        # The run keeps the status its result gave it: `RunStatus` has no `suspended`, and the
        # contract is not widened for a state the work item already shows (`needs_human`).
        suspend = getattr(executor, "suspend")  # noqa: B009 - not in the `Executor` protocol
        suspended = bool(await suspend(_ref(run, _namespace(bundle.slug)), until))
        return {"suspended": suspended, "max_hours": hours, "until": until.isoformat()}


@activity.defn(name="release_suspended_run")
async def release_suspended_run(payload: dict[str, Any]) -> dict[str, Any]:
    """The bound passed with no answer: the sandbox is destroyed, the answer will replay the
    stage. Deleting what is already gone is not an error."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, payload["run_id"])
        if run is None:
            return {"released": False}
        try:
            await bundle.adapters.executor.cancel(_ref(run, _namespace(bundle.slug)))
        except Exception as exc:  # the controller may have deleted it at the same instant
            activity.logger.warning("suspended sandbox not released: %s", exc)
        return {"released": True}


@activity.defn(name="resume_run")
async def resume_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Resumes the suspended run with the answer; `resumed: False` → replay the stage.

    The run is reopened: its result is cleared (the runner reads its input again), the answer is
    put at the head of its prompt, a fresh run token and a fresh gateway key replace the ones
    that expired or were revoked during the wait. Its spend so far stays on the run; the next
    `collect_spend` adds the continuation to it instead of counting a second run.
    """
    answer_id = str(payload.get("answer_id") or "")
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, payload["run_id"])
        if run is None or not run.stage_input:
            return {"resumed": False}
        tokens = dict(run.tokens or {})
        if answer_id and tokens.get("resumed_for") == answer_id and run.result is None:
            return {"resumed": True, "budget": run.stage_input["budget"]}  # replayed activity
        executor = bundle.adapters.executor
        if not _can_suspend(executor):
            return {"resumed": False}
        item = await load_work_item(session, run.work_item_id)
        stage_input = dict(run.stage_input)
        budget = dict(stage_input["budget"])
        timeout_minutes = int(budget["max_minutes"]) + 10
        token = mint_run_token(
            run.id,
            project_slug=bundle.slug,
            work_item_key=item.tracker_key,
            ttl_minutes=timeout_minutes + 15,
        )
        until = utcnow() + timedelta(minutes=timeout_minutes)
        spent = float(run.cost_usd or 0.0)
        key = await mint_key(
            bundle,
            {
                "project": bundle.slug,
                "work_item": item.tracker_key,
                "run_id": run.id,
                "transition": run.transition_id,
                "resumed": int(tokens.get("resumed", 0)) + 1,
            },
            budget_usd=max(float(budget["usd"]) - spent, 0.01),
            ttl_s=key_ttl_seconds(int(budget["max_minutes"])),
            models=[run.model] if run.model else [],
        )
        # The fresh key goes to the sandbox's Secret with the fresh token, never to the row (#523).
        resume = getattr(executor, "resume")  # noqa: B009 - not in the `Executor` protocol
        if not await resume(_ref(run, _namespace(bundle.slug)), token, until, gateway_key=key.key):
            await revoke(bundle, key.key_id)  # nothing will use it
            return {"resumed": False}
        session.add(
            GatewayKeyRow(
                key_id=key.key_id,
                run_id=run.id,
                project_id=bundle.project.id,
                budget_usd=max(float(budget["usd"]) - spent, 0.01),
                expires_at=key.expires_at,
            )
        )
        answer = str((item.documents or {}).get("human_answer") or "").strip()
        playbook = dict(stage_input.get("playbook") or {})
        if answer and playbook.get("prompt") is not None:
            playbook["prompt"] = f"{ANSWER_HEADING}\n\n{answer}\n\n---\n\n{playbook['prompt']}"
        stage_input["playbook"] = playbook
        # The runner reads its input again; its fresh token and key went to the sandbox's Secret
        # through `resume`, never here (#519, #523).
        run.stage_input = without_secrets(stage_input, keep_gateway_key=False)
        run.gateway_key_id = key.key_id
        run.result = None
        run.spend_collected = False
        run.ended_at = None
        run.status = "running"
        run.tokens = {**tokens, "resumed": int(tokens.get("resumed", 0)) + 1, "resumed_for": answer_id}
        return {"resumed": True, "budget": budget}
