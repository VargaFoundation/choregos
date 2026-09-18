"""Interpréteur local : la même logique de décision, sans serveur Temporal.

Il sert deux usages honnêtes :
- `make demo` — montrer la chaîne complète sur un poste, sans cluster ;
- `choregos dev simulate` — rejouer un ticket pour comprendre un workflow.

Il n'a **pas** les garanties de Temporal (reprise après panne, historique, signaux
durables) : en production, c'est `WorkflowInterpreter` qui s'exécute.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import StageResult, StageStatus, Workflow
from choregos_core import GateOutcome, WorkflowEngine

from .activities import findings as finding_activities
from .activities import gates as gate_activities
from .activities import scm as scm_activities
from .activities import stage as stage_activities
from .activities import tracker as tracker_activities
from .workflows.interpreter import load_context

Decider = Callable[[str, str], bool]
StateHook = Callable[[str], Awaitable[None]]


@dataclass
class Step:
    """Une étape jouée par l'interpréteur local, telle qu'on la raconte."""

    state: str
    transition: str
    kind: str
    outcome: str
    next_state: str
    cost_usd: float = 0.0
    run_id: str | None = None


@dataclass
class LocalRun:
    """Trace complète d'une simulation."""

    steps: list[Step] = field(default_factory=list)
    final_state: str = ""
    cost_usd: float = 0.0
    stopped_reason: str = ""

    def summary(self) -> str:
        lines = [f"{'#':>2}  {'état':<24} {'transition':<18} {'acteur':<8} suite"]
        for index, step in enumerate(self.steps, start=1):
            lines.append(
                f"{index:>2}  {step.state:<24} {step.transition:<18} {step.kind:<8} → {step.next_state}"
                + (f"  ({step.cost_usd:.2f} USD)" if step.cost_usd else "")
            )
        lines.append(f"\nétat final : {self.final_state} · coût total : {self.cost_usd:.2f} USD")
        if self.stopped_reason:
            lines.append(f"arrêt : {self.stopped_reason}")
        return "\n".join(lines)


async def run_local(
    project_id: str,
    work_item_id: str,
    *,
    approve: Decider | None = None,
    max_steps: int = 30,
    deploy_ok: bool = True,
    on_state: StateHook | None = None,
) -> LocalRun:
    """Déroule le workflow d'un ticket en appelant les vraies activités."""
    context = await load_context({"project_id": project_id, "work_item_id": work_item_id})
    engine = WorkflowEngine(Workflow.model_validate(context["workflow"]))
    state = context.get("state") or engine.initial_state
    trace = LocalRun()
    attempts: dict[str, int] = {}
    last_outcome: str | None = None
    last_run: str | None = None  # les transitions `system` évaluent leurs gates sur ce run
    approve = approve or (lambda _transition, _state: True)

    await tracker_activities.mirror_state(
        {"project_id": project_id, "work_item_id": work_item_id, "state": state, "reason": "démarrage"}
    )
    if on_state is not None:
        await on_state(state)

    for _ in range(max_steps):
        if engine.is_terminal(state):
            break
        transition = engine.select_transition(state, last_outcome)
        last_outcome = None
        if transition is None:
            trace.stopped_reason = f"aucune transition depuis `{state}` (attente d'un événement externe)"
            break
        kind = engine.actor_kind(transition)
        key = transition.key
        attempt = attempts.get(key, 0)

        if kind == "agent":
            decision, cost, run_id = await _run_stage(project_id, work_item_id, engine, transition, attempt)
            attempts[key] = attempt + 1
            last_run = run_id
            trace.cost_usd += cost
            trace.steps.append(
                Step(state, key, kind, decision.reason, decision.next_state, cost_usd=cost, run_id=run_id)
            )
        elif kind == "human":
            # La demande est réellement créée : commentaire sur le ticket et notification.
            human = engine.human_of(transition)
            request = await tracker_activities.create_human_request(
                {
                    "project_id": project_id,
                    "work_item_id": work_item_id,
                    "transition_id": key,
                    "kind": "approval",
                    "payload": {"summary": f"Validation requise pour passer à `{transition.to}`"},
                    "sla_hours": (human.sla_hours if human else 24),
                }
            )
            approved = approve(key, state)
            await tracker_activities.close_human_request(
                {
                    "request_id": request["request_id"],
                    "decided_by": "simulation",
                    "decision": {"approved": approved, "kind": "approval"},
                }
            )
            decision = engine.after_human(transition, approved, "décision simulée")
            trace.steps.append(Step(state, key, kind, decision.reason, decision.next_state))
        elif kind == "train":
            decision = engine.after_train(transition, ok=deploy_ok)
            trace.steps.append(Step(state, key, kind, decision.reason, decision.next_state))
        else:
            outcomes = await _gates(project_id, work_item_id, transition, last_run)
            if transition.to.startswith("pr_"):
                await scm_activities.open_pull_request(
                    {"project_id": project_id, "work_item_id": work_item_id}
                )
            decision = engine.after_gates(transition, outcomes, attempt)
            attempts[key] = attempt + 1
            trace.steps.append(Step(state, key, kind, decision.reason, decision.next_state))

        if decision.next_state == state and not decision.retried:
            trace.stopped_reason = f"bloqué sur `{state}` : {decision.reason}"
            break
        state = decision.next_state
        await tracker_activities.mirror_state(
            {
                "project_id": project_id,
                "work_item_id": work_item_id,
                "state": state,
                "reason": decision.reason,
            }
        )
        await tracker_activities.update_status_comment(
            {"project_id": project_id, "work_item_id": work_item_id}
        )
        if on_state is not None:
            # Permet au monde extérieur (CI, review, déploiement) de réagir à l'état atteint.
            await on_state(state)

    trace.final_state = state
    if engine.is_terminal(state):
        await tracker_activities.close_out({"project_id": project_id, "work_item_id": work_item_id})
    return trace


async def _run_stage(
    project_id: str, work_item_id: str, engine: WorkflowEngine, transition: Any, attempt: int
) -> tuple[Any, float, str]:
    actor = engine.agent_of(transition)
    assert actor is not None
    prepared = await stage_activities.prepare_stage(
        {
            "project_id": project_id,
            "work_item_id": work_item_id,
            "transition_id": transition.key,
            "role": str(actor.role),
            "from_state": transition.from_,
            "to_state": transition.to,
            "actor": transition.by or "",
            "attempt": attempt + 1,
            "backend": actor.backend,
            "model_request": actor.model,
            "playbook": actor.playbook,
            "outputs": list(transition.outputs),
            "inputs": list(transition.inputs),
        }
    )
    run_id = prepared["run_id"]
    await stage_activities.start_run(
        {"project_id": project_id, "work_item_id": work_item_id, "stage_input": prepared["stage_input"]}
    )
    awaited = await stage_activities.await_run(
        {"project_id": project_id, "run_id": run_id, "timeout_minutes": 1, "poll_seconds": 0.05}
    )
    result = StageResult.model_validate(awaited["result"])
    spend = await stage_activities.collect_spend({"project_id": project_id, "run_id": run_id})
    await stage_activities.record_run_outcome(
        {"project_id": project_id, "run_id": run_id, "result": awaited["result"]}
    )
    await scm_activities.collect_run_artifacts(
        {"project_id": project_id, "work_item_id": work_item_id, "run_id": run_id}
    )
    outcomes = await _gates(project_id, work_item_id, transition, run_id)
    await _emit_findings(project_id, work_item_id, run_id, result)
    decision = engine.after_stage(transition, result, attempt + 1, outcomes)
    if result.status is StageStatus.NEEDS_HUMAN:
        await tracker_activities.create_human_request(
            {
                "project_id": project_id,
                "work_item_id": work_item_id,
                "transition_id": transition.key,
                "kind": "question" if result.questions else "approval",
                "payload": {"summary": result.summary},
            }
        )
    return decision, float(spend.get("cost_usd", 0.0)), run_id


async def _emit_findings(project_id: str, work_item_id: str, run_id: str, result: StageResult) -> None:
    """Déclenche le triage des findings du run.

    Les lignes sont déjà enregistrées par `record_run_outcome` (ou par le runner pendant
    le run) ; ici on joue simplement le workflow `FindingsTriage`, qui les déduplique et
    crée les tickets liés.
    """
    if not result.findings:
        return
    from sqlalchemy import select

    from choregos_api.db.models import Finding, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        slug = project.slug if project else project_id
        pending = (
            (
                await session.execute(
                    select(Finding).where(Finding.origin_run_id == run_id, Finding.status == "pending")
                )
            )
            .scalars()
            .all()
        )
        finding_ids = [row.id for row in pending]
    for finding_id in finding_ids:
        await finding_activities.triage_finding({"project_slug": slug, "finding_id": finding_id})


async def _gates(
    project_id: str, work_item_id: str, transition: Any, run_id: str | None
) -> list[GateOutcome]:
    if not transition.gates:
        return []
    raw = await gate_activities.evaluate_gates(
        {
            "project_id": project_id,
            "work_item_id": work_item_id,
            "run_id": run_id,
            "gates": [{"name": g.name, "params": g.params} for g in transition.gates],
        }
    )
    return [
        GateOutcome(
            name=item["name"],
            passed=item["passed"],
            pending=item["pending"],
            detail=item["detail"],
            annotations=list(item.get("annotations", [])),
        )
        for item in raw
    ]
