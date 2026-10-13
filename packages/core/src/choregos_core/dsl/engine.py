# SPDX-License-Identifier: Apache-2.0
"""Moteur du DSL : la logique de décision de l'interpréteur, **sans Temporal**.

L'orchestrateur (apps/orchestrator) se contente d'appeler ces fonctions pures, ce qui
permet de tester tout le comportement d'un workflow sans serveur Temporal ni base.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from choregos_contracts import (
    AgentActor,
    HumanActor,
    StageResult,
    StageStatus,
    SystemActor,
    Transition,
    Workflow,
    missing_outputs,
)

from ..gates import GateOutcome
from .legacy import legacy_outputs


@dataclass(slots=True, frozen=True)
class Decision:
    """Ce que l'interpréteur doit faire après une étape."""

    next_state: str
    retried: bool = False
    escalated: bool = False
    reason: str = ""

    def __str__(self) -> str:
        suffix = " (escalade)" if self.escalated else (" (nouvelle tentative)" if self.retried else "")
        return f"→ {self.next_state}{suffix} : {self.reason}"


# Résultats possibles d'une étape, utilisés pour choisir la transition suivante.
OUTCOME_CI_FAILED = "ci_failed"
OUTCOME_CHANGES_REQUESTED = "changes_requested"
OUTCOME_APPROVED = "approved"
OUTCOME_REJECTED = "rejected"


class WorkflowEngine:
    """Vue calculatoire d'un workflow : sélection de transition et suites d'une étape."""

    def __init__(self, workflow: Workflow) -> None:
        self.wf = workflow

    # ───────────────────────── sélection ─────────────────────────

    @property
    def initial_state(self) -> str:
        return self.wf.initial_state

    def is_terminal(self, state: str) -> bool:
        return self.wf.states[state].terminal

    def select_transition(self, state: str, outcome: str | None = None) -> Transition | None:
        """Choisit la transition à jouer depuis `state`, de façon **déterministe**.

        Ordre de préférence :
        1. la transition dont l'`id` vaut exactement `outcome` ;
        2. pour `ci_failed`, la transition portée par un agent de rôle `fix_ci` ;
        3. pour `changes_requested`, la transition portée par un agent `address_review` ;
        4. sinon, la première transition déclarée depuis cet état (hors transitions de
           réparation, qui ne se jouent que sur un résultat explicite).
        """
        candidates = self.wf.transitions_from(state)
        if not candidates:
            return None
        if outcome:
            for t in candidates:
                if t.id == outcome:
                    return t
            role = {OUTCOME_CI_FAILED: "fix_ci", OUTCOME_CHANGES_REQUESTED: "address_review"}.get(outcome)
            if role:
                for t in candidates:
                    actor = self.wf.actor_of(t)
                    if isinstance(actor, AgentActor) and str(actor.role) == role:
                        return t
        repair_roles = {"fix_ci", "address_review"}
        for t in candidates:
            actor = self.wf.actor_of(t)
            if isinstance(actor, AgentActor) and str(actor.role) in repair_roles:
                continue
            return t
        return candidates[0]

    def actor_kind(self, transition: Transition) -> str:
        if transition.via == "release_train":
            return "train"
        actor = self.wf.actor_of(transition)
        if isinstance(actor, AgentActor):
            return "agent"
        if isinstance(actor, HumanActor):
            return "human"
        if isinstance(actor, SystemActor):
            return "system"
        return "unknown"

    def agent_of(self, transition: Transition) -> AgentActor | None:
        actor = self.wf.actor_of(transition)
        return actor if isinstance(actor, AgentActor) else None

    def human_of(self, transition: Transition) -> HumanActor | None:
        actor = self.wf.actor_of(transition)
        return actor if isinstance(actor, HumanActor) else None

    # ───────────────────────── suites d'une étape ─────────────────────────

    def max_attempts(self, transition: Transition) -> int:
        retry = transition.on_fail or transition.on_changes_requested
        return retry.max_attempts if retry else 1

    def retry_or_escalate(self, transition: Transition, attempts: int, reason: str = "") -> Decision:
        """Rejoue la transition tant que `max_attempts` n'est pas atteint, puis escalade."""
        retry = transition.on_fail or transition.on_changes_requested
        if retry is None:
            fallback = self._default_state("on_question") or transition.from_
            return Decision(fallback, escalated=True, reason=reason or "failed, with no retry policy")
        if attempts < retry.max_attempts:
            return Decision(
                retry.to, retried=True, reason=reason or f"tentative {attempts + 1}/{retry.max_attempts}"
            )
        return Decision(
            retry.escalate_to,
            escalated=True,
            reason=reason or f"{retry.max_attempts} attempts exhausted",
        )

    def escalate(self, transition: Transition, reason: str) -> Decision:
        """Escalates now, as `retry_or_escalate` does once its attempts are spent (S27-07)."""
        retry = transition.on_fail or transition.on_changes_requested
        target = retry.escalate_to if retry is not None else None
        target = target or self._default_state("on_question") or transition.from_
        return Decision(target, escalated=True, reason=reason)

    def after_stage(
        self,
        transition: Transition,
        result: StageResult,
        attempts: int,
        gate_outcomes: list[GateOutcome] | None = None,
        *,
        require_outputs: bool = True,
    ) -> Decision:
        """Décide de l'état suivant après un run d'agent (docs/plan/02 §2.1).

        `require_outputs`: a `done` result that lacks a declared output fails the stage, whatever
        the output's type (S31-02, ADR 0049) — with or without an `outputs_present` gate. False
        only for histories recorded before that rule (the interpreter's patch marker)."""
        gate_outcomes = gate_outcomes or []
        if result.status is StageStatus.NEEDS_HUMAN:
            target = self._default_state("on_question")
            if target:
                return Decision(target, reason=result.reason or "the agent asks a person to arbitrate")
            return self.retry_or_escalate(transition, attempts, "the agent asks a person to arbitrate")
        if result.status is not StageStatus.DONE:
            if result.reason == "budget":
                target = self._default_state("on_budget_exceeded")
                if target:
                    return Decision(target, reason="budget exceeded")
            if result.reason in {"limit", "timeout"}:
                target = self._default_state("on_timeout")
                if target:
                    return Decision(target, reason=f"limite atteinte ({result.reason})")
            return self.retry_or_escalate(transition, attempts, result.reason or str(result.status))

        if require_outputs:
            # An output an old core template declared and nothing produces is not required: a
            # project's modified copy keeps working as it did (`legacy.py`).
            legacy = set(legacy_outputs(transition.id, list(transition.outputs)))
            required = [name for name in transition.outputs if name not in legacy]
            missing = missing_outputs(required, dict(transition.output_types), result)
            if missing:
                return self.retry_or_escalate(
                    transition, attempts, f"missing declared outputs: {', '.join(missing)}"
                )

        blocking = [g for g in gate_outcomes if g.blocking]
        if blocking:
            names = ", ".join(f"{g.name} ({g.detail})" for g in blocking)
            return self.retry_or_escalate(transition, attempts, f"failing gates: {names}")
        pending = [g for g in gate_outcomes if g.pending]
        if pending:
            names = ", ".join(g.name for g in pending)
            return Decision(transition.from_, reason=f"pending gates: {names}")
        return Decision(transition.to, reason=result.summary[:200])

    def after_budget_exceeded(self, transition: Transition, detail: str = "") -> Decision:
        """A stage stopped on budget goes to a person, **never** to a retry (ADR 0047).

        `on_budget_exceeded` when the workflow declares it, else `on_question`, else the retry
        policy's `escalate_to`, else the state the stage started from. A retry would mint a fresh
        key at the full stage budget: the very overspend the budget was there to stop.
        """
        retry = transition.on_fail or transition.on_changes_requested
        budget = self.wf.budget
        target = (
            (budget.on_exceeded if budget is not None else None)
            or self._default_state("on_budget_exceeded")
            or self._default_state("on_question")
            or (retry.escalate_to if retry else None)
            or transition.from_
        )
        return Decision(target, escalated=True, reason=f"budget: {detail}" if detail else "budget")

    def after_human(self, transition: Transition, approved: bool, reason: str = "") -> Decision:
        if approved:
            return Decision(transition.to, reason=reason or "human decision: approved")
        if transition.on_reject:
            return Decision(transition.on_reject, reason=reason or "human decision: sent back")
        return Decision(transition.from_, reason=reason or "human decision: sent back (to the current state)")

    def after_train(self, transition: Transition, ok: bool, rollback_state: str | None = None) -> Decision:
        if ok:
            return Decision(transition.to, reason="deployment verified")
        target = rollback_state or self._default_state("on_question") or transition.from_
        return Decision(target, escalated=True, reason="deployment failed or aborted")

    def after_gates(self, transition: Transition, outcomes: list[GateOutcome], attempts: int) -> Decision:
        """Suites d'une transition `system` : uniquement des gates."""
        blocking = [g for g in outcomes if g.blocking]
        if blocking:
            return self.retry_or_escalate(
                transition, attempts, "failing gates: " + ", ".join(g.name for g in blocking)
            )
        if any(g.pending for g in outcomes):
            return Decision(transition.from_, reason="pending gates")
        return Decision(transition.to, reason="gates green")

    def after_action(
        self, transition: Transition, status: str, outcomes: list[GateOutcome], attempts: int
    ) -> Decision:
        """Suites d'une transition à action (S20-05). Rejetée, l'action suit `on_reject` quand la
        transition en déclare un ; sinon elle est une garantie comme les autres — `action_succeeded`,
        implicite —, et un échec suit `on_fail`, que le validateur exige : sans lui, le ticket
        reviendrait à son état et proposerait la même action, sans fin."""
        from ..gates import GateContext, evaluate

        if status == "rejected" and transition.on_reject:
            return Decision(transition.on_reject, reason="action rejected")
        verdict = evaluate("action_succeeded", GateContext(action_status=status))
        autres = [o for o in outcomes if o.name != "action_succeeded"]
        return self.after_gates(transition, [verdict, *autres], attempts)

    def timeout_state(self) -> str | None:
        return self._default_state("on_timeout")

    def question_state(self) -> str | None:
        return self._default_state("on_question")

    def abandon_state(self) -> str | None:
        defaults = self.wf.defaults
        if defaults and defaults.needs_human:
            return defaults.needs_human.on_abandon
        return None

    def _default_state(self, attr: str) -> str | None:
        defaults = self.wf.defaults
        if defaults and defaults.from_any_agent_state:
            value = getattr(defaults.from_any_agent_state, attr, None)
            return str(value) if value else None
        return None

    # ───────────────────────── migration ─────────────────────────

    def can_migrate_to(
        self, other: Workflow, current_state: str, mapping: dict[str, str] | None = None
    ) -> str:
        """État correspondant dans une autre définition, ou erreur explicite."""
        mapping = mapping or {}
        target = mapping.get(current_state, current_state)
        if target not in other.states:
            raise ValueError(
                f"the current state `{current_state}` does not exist in {other.metadata.name}"
                f"@{other.metadata.version}, and no mapping was given"
            )
        return target

    def summary(self) -> dict[str, Any]:
        return {
            "name": self.wf.metadata.name,
            "version": self.wf.metadata.version,
            "states": len(self.wf.states),
            "transitions": len(self.wf.transitions),
            "initial": self.initial_state,
            "terminals": self.wf.terminal_states(),
        }


_NUMBERS = re.compile(r"\d+")


def failure_signature(result: StageResult) -> str | None:
    """What makes two failed attempts the same failure (S27-07): their reason and the first line of
    their summary, numbers blanked (a line number, a duration, a count). `None` for a success."""
    if result.status is StageStatus.DONE:
        return None
    first = (result.summary or "").strip().splitlines()[0] if (result.summary or "").strip() else ""
    return f"{result.reason or result.status}:{_NUMBERS.sub('#', first)[:300]}"
