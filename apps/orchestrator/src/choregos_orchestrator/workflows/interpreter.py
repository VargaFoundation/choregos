"""`WorkflowInterpreter` : exécute le DSL épinglé pour un ticket (docs/plan/02 §2.1).

Le workflow ne fait **aucune** entrée/sortie et n'appelle **jamais** un modèle : il décide,
les activités agissent. Toute la logique de décision vient de `choregos_core.WorkflowEngine`,
ce qui la rend testable sans Temporal et rejouable sans surprise.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

#: Attente maximale d'un run EN FILE (six heures), la même valeur que
#: `activities.stage.FILE_MAX_MINUTES` — écrite ici parce qu'un workflow n'importe pas
#: le module des activités, et parce qu'elle entre dans des bornes Temporal.
FILE_MAX_MINUTES = 360

with workflow.unsafe.imports_passed_through():
    from choregos_contracts import StageResult, StageStatus, Workflow
    from choregos_core import WorkflowEngine

    from ..activities import gates as gate_activities
    from ..activities import scm as scm_activities
    from ..activities import stage as stage_activities
    from ..activities import tracker as tracker_activities
    from ..activities.interpretation import (
        activite_de,
        load_context,
        message_de,
        record_workflow_failure,
        signal_train,
    )

DEFAULT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=5,
)
NO_RETRY = RetryPolicy(maximum_attempts=1)
#: Pour une activité qui ne fait que SCRUTER (`await_run`) : idempotente, donc rejouable.
#: Elle tournait sans retry, et un redémarrage du worker pendant un run — un `helm upgrade`,
#: banc du 2026-09-24 — la faisait expirer sur son heartbeat et tuait le ticket.
POLL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=20,
)
HISTORY_THRESHOLD = 20_000


@dataclass
class InterpreterInput:
    """Ce qu'il faut pour démarrer (ou reprendre) l'interprétation d'un ticket."""

    project_id: str
    project_slug: str
    work_item_id: str
    tracker_key: str
    resume_from: str | None = None
    attempts: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    history_threshold: int = HISTORY_THRESHOLD


@dataclass
class InterpreterStatus:
    state: str
    cost_usd: float
    attempts: dict[str, int]
    current_run: str | None
    paused: bool
    stopped: bool
    pending_request: str | None = None


@workflow.defn(name="WorkflowInterpreter", sandboxed=False)
class WorkflowInterpreter:
    """Un workflow par ticket, ID déterministe `wi-<project>-<tracker_key>`."""

    def __init__(self) -> None:
        self.inbox: deque[dict[str, Any]] = deque()
        self.decisions: deque[dict[str, Any]] = deque()
        self.attempts: Counter[str] = Counter()
        self.state: str = ""
        self.cost_usd: float = 0.0
        self.paused: bool = False
        self.stopped: bool = False
        self.current_run: str | None = None
        self.last_run: str | None = None
        self.pending_request: str | None = None
        self.last_outcome: str | None = None
        self.workflow_override: dict[str, Any] | None = None
        self.state_mapping: dict[str, str] = {}
        self.findings_count: int = 0

    # ───────────────────────── signaux et requêtes ─────────────────────────

    @workflow.signal
    def human_decision(self, decision: dict[str, Any]) -> None:
        self.decisions.append(decision)

    @workflow.signal
    def inbound(self, event: dict[str, Any]) -> None:
        self.inbox.append(event)

    @workflow.signal
    def question_asked(self, payload: dict[str, Any]) -> None:
        self.pending_request = str(payload.get("request_id") or "")

    @workflow.signal
    def finding(self, payload: dict[str, Any]) -> None:
        self.findings_count += 1

    @workflow.signal
    def control(self, message: dict[str, Any]) -> None:
        action = message.get("action")
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
        elif action == "stop":
            self.stopped = True
            self.paused = False
        elif action == "migrate":
            self.workflow_override = message.get("workflow", message)
            self.state_mapping = dict(message.get("state_mapping", {}))

    @workflow.query
    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "cost_usd": self.cost_usd,
            "attempts": dict(self.attempts),
            "current_run": self.current_run,
            "last_run": self.last_run,
            "paused": self.paused,
            "stopped": self.stopped,
            "pending_request": self.pending_request,
        }

    # ───────────────────────── boucle principale ─────────────────────────

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = InterpreterInput(**payload)
        try:
            return await self._run(params, payload)
        except (asyncio.CancelledError, workflow.ContinueAsNewError):
            raise
        except Exception as exc:
            # Le workflow meurt : le dire AVANT de mourir. Sans cette trace, le ticket reste
            # dans son état d'origine et rien — ni écran, ni événement — ne distingue « il
            # attend » de « il est mort ». Le banc du 2026-09-24 a perdu deux tickets ainsi.
            with contextlib.suppress(Exception):
                await workflow.execute_activity(
                    record_workflow_failure,
                    {
                        "project_id": params.project_id,
                        "work_item_id": params.work_item_id,
                        "message": message_de(exc),
                        "activity": activite_de(exc),
                        "state": self.state,
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1)),
                )
            raise

    async def _run(self, params: InterpreterInput, payload: dict[str, Any]) -> dict[str, Any]:
        self.attempts.update(params.attempts)
        self.cost_usd = params.cost_usd

        context = await workflow.execute_activity(
            load_context,
            {"project_id": params.project_id, "work_item_id": params.work_item_id},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        engine = WorkflowEngine(Workflow.model_validate(context["workflow"]))
        self.state = params.resume_from or context.get("state") or engine.initial_state

        await self._mirror(params, "démarrage")

        while not engine.is_terminal(self.state) and not self.stopped:
            await workflow.wait_condition(lambda: not self.paused or self.stopped)
            if self.stopped:
                break
            if self.workflow_override is not None:
                engine = self._migrate(engine)
                continue

            transition = engine.select_transition(self.state, self.last_outcome)
            self.last_outcome = None
            if transition is None:
                moved = await self._wait_external(engine, context)
                if moved is None:
                    break
                self.state = moved
                await self._mirror(params, "événement externe")
                continue

            kind = engine.actor_kind(transition)
            if kind == "agent":
                decision = await self._run_stage(params, engine, transition, context)
            elif kind == "human":
                decision = await self._wait_human(params, engine, transition)
            elif kind == "train":
                decision = await self._run_train(params, engine, transition)
            else:
                decision = await self._run_system(params, engine, transition)

            if decision is None:
                continue
            self.state = decision.next_state
            await self._mirror(params, decision.reason)

            if workflow.info().get_current_history_length() > params.history_threshold:
                workflow.continue_as_new(
                    {
                        **payload,
                        "resume_from": self.state,
                        "attempts": dict(self.attempts),
                        "cost_usd": self.cost_usd,
                    }
                )

        await workflow.execute_activity(
            tracker_activities.close_out,
            {"project_id": params.project_id, "work_item_id": params.work_item_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        return {"state": self.state, "cost_usd": self.cost_usd, "stopped": self.stopped}

    # ───────────────────────── étapes ─────────────────────────

    async def _run_stage(
        self, params: InterpreterInput, engine: WorkflowEngine, transition: Any, context: dict[str, Any]
    ) -> Any:
        actor = engine.agent_of(transition)
        if actor is None:
            return engine.retry_or_escalate(
                transition, self.attempts[transition.key], "acteur agent introuvable"
            )
        key = transition.key
        attempt = self.attempts[key]
        self.attempts[key] = attempt + 1

        prepared = await workflow.execute_activity(
            stage_activities.prepare_stage,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "transition_id": key,
                "role": str(actor.role),
                "from_state": transition.from_,
                "to_state": transition.to,
                "actor": transition.by or "",
                "attempt": attempt + 1,
                "backend": actor.backend,
                "model_request": actor.model,
                "fresh_context": actor.fresh_context,
                "max_turns": actor.max_turns,
                "max_minutes": actor.max_minutes,
                "playbook": actor.playbook,
                "outputs": list(transition.outputs),
                "inputs": list(transition.inputs),
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        run_id = prepared["run_id"]
        self.current_run = run_id
        budget = prepared["stage_input"]["budget"]

        await workflow.execute_activity(
            stage_activities.start_run,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "stage_input": prepared["stage_input"],
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        awaited = await workflow.execute_activity(
            stage_activities.await_run,
            {
                "project_id": params.project_id,
                "run_id": run_id,
                "timeout_minutes": int(budget["max_minutes"]) + 10,
            },
            start_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 20),
            # Borne GLOBALE, toutes tentatives comprises : sans elle, vingt reprises d'une
            # attente de deux heures feraient un ticket qui ne meurt jamais.
            schedule_to_close_timeout=timedelta(minutes=int(budget["max_minutes"]) + 90),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=POLL_RETRY,
        )
        result = StageResult.model_validate(awaited["result"])

        spend = await workflow.execute_activity(
            stage_activities.collect_spend,
            {"project_id": params.project_id, "run_id": run_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        self.cost_usd += float(spend.get("cost_usd", 0.0))
        await workflow.execute_activity(
            stage_activities.record_run_outcome,
            {"project_id": params.project_id, "run_id": run_id, "result": awaited["result"]},
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        # Le diff du run est ce que le front AFFICHE — pas ce dont le ticket dépend. Un échec
        # ici (SCM injoignable, projet sans dépôt sur une image en retard) ne doit pas tuer une
        # étape qui vient de réussir : c'est arrivé à RH-1 le 2026-09-24.
        try:
            await workflow.execute_activity(
                scm_activities.collect_run_artifacts,
                {"project_id": params.project_id, "work_item_id": params.work_item_id, "run_id": run_id},
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=NO_RETRY,
            )
        except ActivityError as exc:
            workflow.logger.warning("diff du run non collecté : %s", message_de(exc))
        self.current_run = None
        self.last_run = run_id  # les transitions `system` évaluent leurs gates sur ce run

        outcomes = await self._gates(params, transition, run_id)

        if self.cost_usd > float(context.get("ticket_budget_usd", 0.0)) > 0:
            result = result.model_copy(
                update={
                    "status": StageStatus.NEEDS_HUMAN,
                    "reason": "budget",
                    "summary": f"{result.summary} (budget du ticket dépassé)",
                }
            )
        for request in result.scope_changes_requested:
            await self._request_scope_change(params, transition, request)

        decision = engine.after_stage(transition, result, self.attempts[key], outcomes)
        if decision.next_state == engine.question_state() and result.status is StageStatus.NEEDS_HUMAN:
            await self._create_request(
                params,
                transition,
                kind="question" if result.questions else "approval",
                payload={
                    "question": result.questions[0].text if result.questions else result.summary,
                    "options": list(result.questions[0].options) if result.questions else [],
                    "summary": result.summary,
                    "run_id": run_id,
                },
            )
        return decision

    async def _gates(self, params: InterpreterInput, transition: Any, run_id: str) -> list[Any]:
        if not transition.gates:
            return []
        from choregos_core import GateOutcome

        raw = await workflow.execute_activity(
            gate_activities.evaluate_gates,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "run_id": run_id,
                # Ce que la transition déclare produire : la gate générique `outputs_present`
                # s'en sert quand elle n'a pas de liste explicite.
                "expected_outputs": list(transition.outputs),
                "gates": [{"name": g.name, "params": g.params} for g in transition.gates],
            },
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=DEFAULT_RETRY,
        )
        outcomes = [
            GateOutcome(
                name=item["name"],
                passed=item["passed"],
                pending=item["pending"],
                detail=item["detail"],
                annotations=list(item.get("annotations", [])),
            )
            for item in raw
        ]
        pending = [o for o in outcomes if o.pending]
        if pending and transition.timeout_hours:
            resolved = await self._await_async_gates(params, transition, run_id, [o.name for o in pending])
            outcomes = [o for o in outcomes if not o.pending] + resolved
        return outcomes

    async def _await_async_gates(
        self, params: InterpreterInput, transition: Any, run_id: str, names: list[str]
    ) -> list[Any]:
        """Attend les événements externes (CI, review) puis réévalue, jusqu'au timeout."""
        from choregos_core import GateOutcome

        deadline = timedelta(hours=transition.timeout_hours or 24)
        waited = timedelta()
        step = timedelta(minutes=5)
        while waited < deadline:
            received = await wait_signal(lambda: bool(self.inbox) or self.stopped, step)
            while self.inbox:
                event = self.inbox.popleft()
                self._absorb(event)
            raw = await workflow.execute_activity(
                gate_activities.evaluate_gates,
                {
                    "project_id": params.project_id,
                    "work_item_id": params.work_item_id,
                    "run_id": run_id,
                    "expected_outputs": list(transition.outputs),
                    "gates": [
                        {"name": g.name, "params": g.params} for g in transition.gates if g.name in names
                    ],
                },
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=DEFAULT_RETRY,
            )
            outcomes = [
                GateOutcome(
                    name=item["name"], passed=item["passed"], pending=item["pending"], detail=item["detail"]
                )
                for item in raw
            ]
            if not any(o.pending for o in outcomes) or self.stopped:
                return outcomes
            waited += step
            if received is False:  # wait_condition a expiré sans signal
                continue
        return [GateOutcome(name=name, passed=False, detail="délai dépassé") for name in names]

    def _absorb(self, event: dict[str, Any]) -> None:
        """Traduit un événement entrant en `last_outcome` pour la prochaine sélection."""
        kind = event.get("type", "")
        if (
            kind in {"ci.run.failed", "scm.check.completed"}
            and event.get("payload", {}).get("conclusion") == "failure"
        ):
            self.last_outcome = "ci_failed"
        elif (
            kind == "scm.pr.review_submitted" and event.get("payload", {}).get("state") == "changes_requested"
        ):
            self.last_outcome = "changes_requested"
        elif kind == "scm.pr.merged":
            self.last_outcome = "merged"

    # ───────────────────────── humain ─────────────────────────

    async def _wait_human(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        actor = engine.human_of(transition)
        sla_hours = (actor.sla_hours if actor else None) or 24
        request = await self._create_request(
            params,
            transition,
            kind="approval",
            payload={"summary": f"Validation requise pour passer à `{transition.to}`"},
            sla_hours=sla_hours,
        )
        self.pending_request = request["request_id"]
        timeout = timedelta(hours=transition.timeout_hours or sla_hours * 3)
        reminder_sent = False
        waited = timedelta()
        step = timedelta(hours=1)
        while waited < timeout:
            got = await wait_signal(lambda: bool(self.decisions) or self.stopped, min(step, timeout - waited))
            if self.stopped:
                return None
            if self.decisions:
                decision = self.decisions.popleft()
                await workflow.execute_activity(
                    tracker_activities.close_human_request,
                    {
                        "request_id": decision.get("request_id") or self.pending_request,
                        "decided_by": decision.get("decided_by", "humain"),
                        "decision": decision,
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=DEFAULT_RETRY,
                )
                self.pending_request = None
                approved = bool(decision.get("approved"))
                return engine.after_human(transition, approved, decision.get("reason") or "")
            waited += step
            if not got and not reminder_sent and waited >= timedelta(hours=sla_hours):
                reminder_sent = True
                await self._remind(params, actor, transition)
        escalate = engine.timeout_state() or transition.from_
        return (
            engine.after_human(transition, approved=False, reason="délai d'attente humain dépassé")
            if transition.on_reject
            else _decision(escalate, "délai humain dépassé")
        )

    async def _remind(self, params: InterpreterInput, actor: Any, transition: Any) -> None:
        from choregos_core import Message

        sla = actor.sla_hours if actor else 24

        await workflow.execute_activity(
            tracker_activities.notify,
            {
                "project_id": params.project_id,
                "message": Message(
                    title=f"Rappel : {params.tracker_key} attend une décision",
                    body=f"Transition `{transition.key}` en attente depuis {sla} h.",
                    severity="warning",
                ).model_dump(mode="json"),
            },
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=NO_RETRY,
        )

    async def _create_request(
        self,
        params: InterpreterInput,
        transition: Any,
        *,
        kind: str,
        payload: dict[str, Any],
        sla_hours: int = 24,
    ) -> dict[str, Any]:
        return await workflow.execute_activity(
            tracker_activities.create_human_request,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "transition_id": transition.key,
                "kind": kind,
                "payload": payload,
                "sla_hours": sla_hours,
            },
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )

    async def _request_scope_change(self, params: InterpreterInput, transition: Any, request: Any) -> None:
        await self._create_request(
            params,
            transition,
            kind="scope_change",
            payload={"paths": list(request.paths), "justification": request.justification},
        )

    # ───────────────────────── système et train ─────────────────────────

    async def _run_system(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        key = transition.key
        attempt = self.attempts[key]
        self.attempts[key] = attempt + 1

        if transition.to.startswith("pr_"):
            await workflow.execute_activity(
                scm_activities.open_pull_request,
                {"project_id": params.project_id, "work_item_id": params.work_item_id},
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=DEFAULT_RETRY,
            )
        outcomes = await self._gates(params, transition, self.current_run or self.last_run or "")
        if transition.to.startswith("merged") and all(not o.blocking and not o.pending for o in outcomes):
            await workflow.execute_activity(
                scm_activities.enqueue_merge,
                {"project_id": params.project_id, "work_item_id": params.work_item_id},
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=DEFAULT_RETRY,
            )
        return engine.after_gates(transition, outcomes, self.attempts[key])

    async def _run_train(self, params: InterpreterInput, engine: WorkflowEngine, transition: Any) -> Any:
        env = transition.train.env if transition.train else "prod"
        await workflow.execute_activity(
            signal_train,
            {
                "project_id": params.project_id,
                "project_slug": params.project_slug,
                "work_item_id": params.work_item_id,
                "env": env,
            },
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        timeout = timedelta(hours=transition.timeout_hours or 72)
        waited = timedelta()
        step = timedelta(minutes=10)
        while waited < timeout and not self.stopped:
            await wait_signal(lambda: bool(self.inbox) or self.stopped, step)
            while self.inbox:
                event = self.inbox.popleft()
                if event.get("type") in {"cd.rollout.completed", "cd.app.synced"}:
                    return engine.after_train(transition, ok=True)
                if event.get("type") in {"cd.rollout.aborted", "cd.app.degraded"}:
                    return engine.after_train(transition, ok=False)
                self._absorb(event)
            waited += step
        return engine.after_train(transition, ok=False)

    # ───────────────────────── utilitaires ─────────────────────────

    def _migrate(self, engine: WorkflowEngine) -> WorkflowEngine:
        """Migration vers une autre définition : l'état courant doit exister, ou être mappé."""
        override = self.workflow_override or {}
        self.workflow_override = None
        definition = override.get("workflow") or override.get("json") or override
        target = Workflow.model_validate(definition)
        new_engine = WorkflowEngine(target)
        self.state = WorkflowEngine(engine.wf).can_migrate_to(target, self.state, self.state_mapping)
        return new_engine

    async def _wait_external(self, engine: WorkflowEngine, context: dict[str, Any]) -> str | None:
        """État d'attente sans transition sortante : c'est le board humain qui décide."""
        while not self.stopped:
            await workflow.wait_condition(lambda: bool(self.inbox) or bool(self.decisions) or self.stopped)
            while self.inbox:
                event = self.inbox.popleft()
                if event.get("type") == "tracker.item.moved":
                    to_status = event.get("payload", {}).get("to_status")
                    for name, state in engine.wf.states.items():
                        if state.tracker and state.tracker.status == to_status:
                            return name
                self._absorb(event)
            while self.decisions:
                decision = self.decisions.popleft()
                if decision.get("approved"):
                    transitions = engine.wf.transitions_from(self.state)
                    if transitions:
                        return transitions[0].to
        return None

    async def _mirror(self, params: InterpreterInput, reason: str) -> None:
        await workflow.execute_activity(
            tracker_activities.mirror_state,
            {
                "project_id": params.project_id,
                "work_item_id": params.work_item_id,
                "state": self.state,
                "reason": reason,
            },
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=DEFAULT_RETRY,
        )
        await workflow.execute_activity(
            tracker_activities.update_status_comment,
            {"project_id": params.project_id, "work_item_id": params.work_item_id},
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )


async def wait_signal(condition: Any, timeout: timedelta | None = None) -> bool:  # noqa: ASYNC109
    """`workflow.wait_condition` qui rend `False` sur expiration au lieu de lever.

    (Dans le SDK Temporal, un `wait_condition` qui expire lève `asyncio.TimeoutError` ;
    ici on veut distinguer « la condition est arrivée » de « le délai est écoulé ».)
    """
    if timeout is None:
        await workflow.wait_condition(condition)
        return True
    try:
        await workflow.wait_condition(condition, timeout=timeout)
    except TimeoutError:
        return False
    return True


def _decision(state: str, reason: str) -> Any:
    from choregos_core import Decision

    return Decision(next_state=state, escalated=True, reason=reason)
