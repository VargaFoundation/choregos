# SPDX-License-Identifier: Apache-2.0
"""The "process" view of a workflow: each transition said in plain words (ADR 0031).

The map (`graph.to_graph`) shows the SHAPE of a workflow; it stayed silent on what matters to a
trade: who moves a work item from one state to the next, under which conditions, and what happens
when it fails. Here, each transition becomes a sentence, and its elements stay structured for
the console. The sentences are in English, like the interface (S5-01).
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import Workflow
from choregos_contracts.workflow import (
    AGENT_WILDCARD,
    AgentActor,
    ChoiceQuestion,
    DecisionActor,
    HumanActor,
    JobActor,
    ScoreQuestion,
    Transition,
    transition_effect,
)

from choregos_core.gates import DIFF_GATES, is_async_gate

#: What each guarantee checks, in one sentence. A test requires a summary for every known
#: guarantee: a new guarantee without a summary would make the process view speak into the void.
RESUMES: dict[str, str] = {
    "action_succeeded": "its governed action succeeded",
    "adr_number_free": "the decision record's number is not already taken on the default branch",
    "already_fixed_proven": "the step changed nothing, pushed no branch, and the tests pass on the default "
    "branch, as measured",
    "ci_green": "the CI pipeline is green",
    "coverage_delta_min": "test coverage does not drop below the threshold",
    "diff_size_max": "the change stays under the size limit",
    "docs_updated": "the change updates its documentation when it changes anything else",
    "evidence_facts": "the facts the step counted meet the declared minimums",
    "evidence_present": "the platform measured the evidence the stage owes",
    "external": "an external check reports success",
    "flag_present": "the change sits behind a feature flag",
    "no_secrets": "no secret leaks into the change",
    "numbers_measured": "every number the step writes was measured by an earlier step",
    "outputs_present": "the declared outputs are present",
    "output_schema": "the outputs match their declared JSON Schema",
    "outputs_in": "the outputs take an allowed value (a review's verdict, a size)",
    "markdown_sections": "the documents the change adds carry their required sections",
    "provenance_signed": "the build provenance is signed",
    "review_approved": "the review is approved",
    "scans_ok": "security scans find nothing blocking",
    "scope_respected": "the agent stayed within its allowed paths",
    "tool_called": "the required tool was actually called",
}


def _listed(values: Any) -> str:
    return ", ".join(f"`{v}`" for v in values)


def gate_detail(name: str, params: dict[str, Any], timeout_hours: int | None = None) -> str | None:
    """What a guarantee holds the step to, with the thresholds it actually applies — the defaults of
    `choregos_core.gates` when the workflow sets none — and how long an asynchronous one waits."""
    parts: list[str] = []
    if name == "diff_size_max":
        files = params.get("files", params.get("n"))
        lines = params.get("lines")
        parts.append(
            f"at most {files if files is not None else 60} files and "
            f"{lines if lines is not None else 100_000} lines changed"
            + (" (the platform's defaults)" if files is None and lines is None else "")
        )
    elif name == "coverage_delta_min":
        threshold = float(params.get("x", params.get("min", 0.0)))
        parts.append(f"coverage may not move by less than {threshold:+.2f} points")
    elif name == "scope_respected":
        paths = params.get("paths")
        parts.append(
            f"only files under {_listed(paths)}"
            if paths
            else "only the work item's allowed paths; with none declared, anything passes"
        )
    elif name == "tool_called" and params.get("tools"):
        parts.append(f"calls {_listed(params['tools'])}")
    elif name == "evidence_facts":
        for key, minimum in (params.get("min") or {}).items():
            parts.append(f"`{key}` ≥ {minimum}")
        for key, maximum in (params.get("max") or {}).items():
            parts.append(f"`{key}` ≤ {maximum}")
        if params.get("must_be_true"):
            parts.append(f"{_listed(params['must_be_true'])} true")
    elif name == "outputs_in":
        for key, allowed in (params.get("values") or {}).items():
            parts.append(f"`{key}` in {_listed(allowed)}")
    elif params:
        parts.append(", ".join(f"{key}: {value}" for key, value in params.items()))
    if is_async_gate(name):
        parts.append(f"waits for it up to {timeout_hours or 24} h")
    return "; ".join(parts) or None


def _display(wf: Workflow, state: str) -> str:
    if state == AGENT_WILDCARD:
        return "any agent step"
    found = wf.states.get(state)
    return found.display if found else state


def _decision(acteur: DecisionActor) -> str:
    """A decision step in plain words (ADR 0121): its model, what it reads, and what it answers."""
    questions = []
    for name, question in acteur.questions.items():
        if isinstance(question, ChoiceQuestion):
            questions.append(f"{name} (choice of {', '.join(question.options)})")
        elif isinstance(question, ScoreQuestion):
            questions.append(f"{name} (score {', '.join(question.levels)})")
        else:
            questions.append(f"{name} (yes/no)")
    return f"a decision by `{acteur.model}` on {', '.join(acteur.state)}: {', '.join(questions)}"


def _human_who(actor: HumanActor) -> str:
    """Who answers a human step, in plain words (ADR 0153): a person, a group, or a person first."""
    if actor.user and actor.group:
        return f"`{actor.user}`, then a person of group `{actor.group}` once the SLA has passed"
    if actor.user:
        return f"`{actor.user}`"
    return f"a person of group `{actor.group}`"


def _who(wf: Workflow, t: Transition) -> tuple[str, str, str]:
    """(actor type, name, description)."""
    if t.via == "release_train":
        env = t.train.env if t.train else "the target environment"
        return "release_train", "release_train", f"the release train to {env}"
    name = t.by or "system"
    actor = wf.actors.get(name)
    if isinstance(actor, AgentActor):
        return "agent", name, f"the agent `{name}` (role {actor.role}, model {actor.model})"
    if isinstance(actor, JobActor):
        return "job", name, f"the job `{name}` (role {actor.role}): `{' '.join(actor.command)}`, no model"
    if isinstance(actor, HumanActor):
        delay = f", within {actor.sla_hours} h" if actor.sla_hours else ""
        return "human", name, f"{_human_who(actor)}{delay}"
    if isinstance(actor, DecisionActor):
        return "decision", name, _decision(actor)
    # What the platform does when it crosses it, in plain words (`does`, #175).
    gesture = {"open_pr": ", which opens the pull request", "merge_pr": ", which merges the pull request"}
    return "system", name, f"the platform{gesture.get(transition_effect(t) or '', '')}"


def _action(t: Transition) -> str | None:
    """The governed action the transition proposes, in plain words: what, when, under which approval."""
    if t.action is None:
        return None
    text = f"proposing the governed action `{t.action.kind}`"
    if t.action.not_before:
        text += f" not before `{t.action.not_before}`"
    if t.action.approval is not None:
        roles = ", ".join(f"{a.min} {a.role}" for a in t.action.approval.approvers)
        text += f", approved by {roles}"
    else:
        text += ", approved by a person when one of its operations requires it"
    return text


def _task(t: Transition) -> str | None:
    """The task a person does, in plain words: what, which fields, what they attest."""
    if t.task is None:
        return None
    fields = ", ".join(f"`{name}`" for name in (t.task.form.get("properties") or {}))
    text = f"done “{t.task.title or t.to}” (filling {fields})"
    if t.task.attest:
        text += f" and attested “{t.task.attest}”"
    return text


def to_process(wf: Workflow) -> list[dict[str, Any]]:
    """One step per transition, in the order of the YAML: who acts, from which state to which, under
    which guarantees, what must be produced, and what happens on failure or rejection."""
    steps = []
    for t in wf.transitions:
        type_, name, who = _who(wf, t)
        names = [g.name for g in t.gates]
        gate_params = {g.name: dict(g.params) for g in t.gates}
        if t.action is not None and "action_succeeded" not in names:
            names.insert(0, "action_succeeded")  # implicit: a transition with an action always waits for it
        guarantees = [
            {
                "name": gate,
                "summary": RESUMES.get(gate, gate),
                "detail": gate_detail(gate, gate_params.get(gate, {}), t.timeout_hours),
                "asynchronous": is_async_gate(gate),
            }
            for gate in names
        ]
        conditions = []
        if t.outputs:
            conditions.append("it produced " + ", ".join(f"`{o}`" for o in t.outputs))
        conditions += [RESUMES.get(gate, gate) for gate in names]
        sentence = f"From “{_display(wf, t.from_)}”, {who} moves the item to “{_display(wf, t.to)}”"
        action = _action(t)
        if action:
            sentence += f", after {action},"
        task = _task(t)
        if task:
            sentence += f", once they have {task},"
        if conditions:
            sentence += " once " + "; ".join(conditions)
        sentence += "."
        failure = None
        if t.on_fail is not None:
            retry_from, escalation = _display(wf, t.on_fail.to), _display(wf, t.on_fail.escalate_to)
            failure = (
                f"On failure it retries up to {t.on_fail.max_attempts} time(s) from “{retry_from}”, "
                f"then goes to “{escalation}”."
            )
            sentence += " " + failure
        rejection = f"If rejected, back to “{_display(wf, t.on_reject)}”." if t.on_reject else None
        if rejection:
            sentence += " " + rejection
        if t.timeout_hours:
            sentence += f" It times out after {t.timeout_hours} h."
        actor = wf.actors.get(t.by) if t.by else None
        # A decision writes its answers and their probabilities: what a `when` compares (ADR 0121).
        outputs = (
            actor.outputs + [o for o in t.outputs if o not in actor.outputs]
            if isinstance(actor, DecisionActor)
            else list(t.outputs)
        )
        steps.append(
            {
                "id": t.key,
                "from": t.from_,
                "from_display": _display(wf, t.from_),
                "to": t.to,
                "to_display": _display(wf, t.to),
                "actor": name,
                "actor_type": type_,
                "who": who,
                "outputs": outputs,
                "gates": guarantees,
                "on_fail": failure,
                "on_reject": rejection,
                "timeout_hours": t.timeout_hours,
                "action": action,
                "task": task,
                "sentence": sentence,
                # What the step names, structured, so the console need not parse the sentence.
                "agent": actor.agent if isinstance(actor, AgentActor) else None,
                "role": str(actor.role) if isinstance(actor, AgentActor | JobActor | DecisionActor) else None,
                "model": actor.model if isinstance(actor, AgentActor | DecisionActor) else None,
                "group": actor.group if isinstance(actor, HumanActor) else None,
                "user": actor.user if isinstance(actor, HumanActor) else None,
                "sla_hours": actor.sla_hours if isinstance(actor, HumanActor) else None,
                "command": list(actor.command) if isinstance(actor, JobActor) else None,
                "image": (str(actor.image) if actor.image else None) if isinstance(actor, JobActor) else None,
                "inputs": list(t.inputs),
                "output_types": dict(t.output_types),
                "writes_branch": isinstance(actor, AgentActor | JobActor)
                and bool(set(t.gate_names()) & DIFF_GATES),
            }
        )
    return steps
