# SPDX-License-Identifier: Apache-2.0
"""Static validation of the workflow DSL (the rules of docs/plan/01 §1.4).

Errors are **blocking**: an invalid workflow is never pinned on a work item.
Warnings do not prevent saving but are reported to the front end and the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import (
    ARTIFACT_NAME_PATTERN,
    ARTIFACT_TYPES,
    CHECKED_TYPES,
    FACT_TYPES,
    MAX_FACTS,
    RESERVED_ARTIFACT_NAMES,
    RUN_ACTORS,
    VALUE_TYPES,
    AgentActor,
    ArtifactSpec,
    DecisionActor,
    HumanActor,
    SystemActor,
    Workflow,
    resolve_display,
)
from choregos_contracts.workflow import (
    AGENT_WILDCARD,
    CONFIDENCE_SUFFIX,
    PRODUCTION_PREFIX,
    Transition,
    implicit_effect,
    is_production_state,
    transition_effect,
)
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ..domain import REPOSITORY_FIELD, TRACKER_FIELD
from ..errors import Issue
from ..gates import ALREADY_FIXED_GATE, known_gates
from .dates import field_of
from .groups import is_per_project, placeholder_error
from .legacy import legacy_outputs
from .yamlsource import json_pointer, locate

#: Still read for workflows written before #175; a production state is written `production: true`.
PROD_STATE_PREFIX = PRODUCTION_PREFIX


@dataclass(slots=True)
class ValidationReport:
    valid: bool = True
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    def error(self, code: str, message: str, path: list[str | int], source: Any = None) -> None:
        line, column = locate(source, path) if source is not None else (None, None)
        self.errors.append(Issue(code, message, json_pointer(path), line, column))
        self.valid = False

    def warn(self, code: str, message: str, path: list[str | int], source: Any = None) -> None:
        line, column = locate(source, path) if source is not None else (None, None)
        self.warnings.append(Issue(code, message, json_pointer(path), line, column))

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
        }


def validate_workflow(wf: Workflow, source: Any = None) -> ValidationReport:
    """Apply the DSL's static rules to an already deserialised workflow."""
    report = ValidationReport()
    states = set(wf.states)
    gates = set(known_gates())

    _check_references(wf, states, gates, report, source)
    _check_initial_and_terminal(wf, report, source)
    _check_reachability(wf, report, source)
    _check_prod_states(wf, report, source)
    _check_retries(wf, report, source)
    _check_gates_have_something_to_check(wf, report, source)
    _check_output_schemas(wf, report, source)
    _check_known_roles(wf, report, source)
    _check_inputs(wf, report, source)
    _check_actions(wf, report, source)
    _check_already_fixed_exits(wf, report, source)
    _check_conditions(wf, report, source)
    _check_decisions(wf, report, source)
    _check_confidence_thresholds(wf, report, source)
    _check_tasks(wf, report, source)
    _check_spawns(wf, report, source)
    _check_effects(wf, report, source)
    _check_output_types(wf, report, source)
    _check_artifact_declarations(wf, report, source)
    _check_unenforced_review(wf, report, source)
    _check_warnings(wf, report, source)
    return report


def _check_output_types(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A typed output is a declared one, of a known type, under a name that can be a file name
    (ADR 0049): a type for an undeclared output would never be checked, and a misspelt type
    would make a file output look like an inline one."""
    import re

    valid_name = re.compile(ARTIFACT_NAME_PATTERN)
    for index, t in enumerate(wf.transitions):
        for name in legacy_outputs(t.id, list(t.outputs)):
            report.warn(
                "outputs.legacy",
                f"`{name}` on `{t.id}` is a declaration of an old core template that nothing produces: "
                "it is not required, and the next step never receives it — remove it, or declare what "
                "the step writes (`spec_markdown`, `plan_markdown`)",
                ["transitions", index, "outputs"],
                source,
            )
        for name, type_ in t.output_types.items():
            location: list[str | int] = ["transitions", index, "output_types", name]
            if name not in t.outputs:
                report.error(
                    "output_types.undeclared",
                    f"`output_types` types `{name}`, which `outputs:` does not declare",
                    location,
                    source,
                )
            if type_ not in ARTIFACT_TYPES:
                report.error(
                    "output_types.unknown",
                    f"unknown type `{type_}` for `{name}`: one of {', '.join(sorted(ARTIFACT_TYPES))}",
                    location,
                    source,
                )
            if not valid_name.fullmatch(name):
                report.error(
                    "output_types.name",
                    f"`{name}` cannot name a file: letters, digits, `_`, `.`, `-`, not starting with a dot",
                    location,
                    source,
                )


#: Outputs a stage's result already types with its own words (`StageOutputs`): another scale could never
#: be answered, and the step would fail however the agent replied.
FIXED_SCALES = frozenset({"size", "risk", "verdict"})


def _check_a_declaration(name: str, spec: ArtifactSpec, report: ValidationReport, source: Any) -> None:
    """One entry of `artifacts:` says something the platform can honour (ADR 0129)."""
    location: list[str | int] = ["artifacts", name]
    resolved = resolve_display(name, spec)
    type_ = resolved.type if resolved is not None else None
    if name in RESERVED_ARTIFACT_NAMES and spec.type not in (None, name):
        report.error(
            "artifacts.reserved",
            f"`{name}` is measured by the platform: a workflow says how it is shown, not what it is",
            [*location, "type"],
            source,
        )
    elif spec.type is not None and spec.type not in CHECKED_TYPES and name not in RESERVED_ARTIFACT_NAMES:
        report.error(
            "artifacts.format",
            f"`{name}` declares the type `{spec.type}`: `artifacts:` says what a value is "
            f"({', '.join(sorted(CHECKED_TYPES))}); a document's format stays in `output_types`",
            [*location, "type"],
            source,
        )
    if name in FIXED_SCALES and (spec.levels is not None or spec.scale not in (None, name)):
        report.error(
            "artifacts.scale",
            f"`{name}` is a field of a stage's result, with its own words: its scale cannot be replaced",
            location,
            source,
        )
    if spec.scale is not None and spec.levels is not None:
        report.error(
            "artifacts.scale",
            f"`{name}` names a scale and writes `levels`: one or the other",
            location,
            source,
        )
    if type_ == "level" and (resolved is None or not resolved.levels):
        report.error(
            "artifacts.scale", f"`{name}` is a `level`: it needs a `scale` or `levels`", location, source
        )
    if (spec.scale is not None or spec.levels is not None) and type_ != "level":
        report.error("artifacts.scale", f"`{name}` has a scale, but it is not a `level`", location, source)
    if spec.levels is not None and len({level.value for level in spec.levels}) != len(spec.levels):
        report.error(
            "artifacts.scale", f"`{name}` repeats a value in `levels`", [*location, "levels"], source
        )
    measures = (
        spec.unit is not None or spec.min is not None or spec.max is not None or spec.better is not None
    )
    if measures and type_ != "metric":
        report.error(
            "artifacts.metric",
            f"`{name}` has a unit, bounds or a direction, but it is not a `metric`",
            location,
            source,
        )
    if spec.min is not None and spec.max is not None and spec.min >= spec.max:
        report.error("artifacts.metric", f"`{name}`: `min` must be below `max`", location, source)
    if spec.show == "facts" and type_ not in FACT_TYPES:
        report.error(
            "artifacts.facts",
            f"`{name}` cannot be shown in the ticket's facts: only what fits on one line can "
            f"({', '.join(sorted(FACT_TYPES))})",
            [*location, "show"],
            source,
        )


def _check_artifact_declarations(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """`artifacts:` names what the workflow produces, says what it can honour, and leaves the reserved
    names to the platform (ADR 0129 §4, §5, §7)."""
    produced = {name for t in wf.transitions for name in t.outputs} | {
        name
        for t in wf.transitions
        if isinstance(actor := wf.actor_of(t), DecisionActor)
        for name in actor.outputs
    }
    for index, t in enumerate(wf.transitions):
        for name in sorted(RESERVED_ARTIFACT_NAMES & set(t.outputs)):
            report.error(
                "outputs.reserved",
                f"`{name}` is measured by the platform: no step may declare it as an output",
                ["transitions", index, "outputs"],
                source,
            )
        for name, type_ in t.output_types.items():
            if type_ in VALUE_TYPES:
                report.error(
                    "output_types.value",
                    f"`{name}` is a `{type_}`: declare it in `artifacts:`, which holds its scale or its unit",
                    ["transitions", index, "output_types", name],
                    source,
                )
    declared = wf.artifacts or {}
    for name, spec in declared.items():
        if name not in produced and name not in RESERVED_ARTIFACT_NAMES:
            report.error(
                "artifacts.undeclared",
                f"`artifacts:` describes `{name}`, which no step of this workflow produces",
                ["artifacts", name],
                source,
            )
        for index, t in enumerate(wf.transitions):
            if name in t.output_types and spec.type is not None and spec.type != t.output_types[name]:
                report.error(
                    "artifacts.conflict",
                    f"`{name}` is `{spec.type}` in `artifacts:`, `{t.output_types[name]}` in `output_types`",
                    ["transitions", index, "output_types", name],
                    source,
                )
        _check_a_declaration(name, spec, report, source)
    facts = [name for name, spec in declared.items() if spec.show == "facts"]
    if len(facts) > MAX_FACTS:
        report.error(
            "artifacts.facts",
            f"{len(facts)} names in the facts card: at most {MAX_FACTS}, or it stops being read",
            ["artifacts"],
            source,
        )


def _check_inputs(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A work item's fields are described by a valid OBJECT JSON Schema (ADR 0031)."""
    schema = wf.metadata.inputs
    if schema is None:
        return
    import jsonschema

    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as error:
        report.error(
            "inputs.invalid",
            f"`metadata.inputs` is not a valid JSON Schema: {error.message}",
            ["metadata", "inputs"],
            source,
        )
        return
    if schema.get("type", "object") != "object":
        report.error(
            "inputs.not_object",
            "`metadata.inputs` describes an object: the work item's fields",
            ["metadata", "inputs"],
            source,
        )
    if TRACKER_FIELD in (schema.get("properties") or {}) or TRACKER_FIELD in (schema.get("required") or []):
        report.error(
            "inputs.reserved",
            f"`{TRACKER_FIELD}` is reserved: the platform puts there what the tracker says about the "
            "ticket (`ticket.fields.tracker`); name the field otherwise",
            ["metadata", "inputs", "properties", TRACKER_FIELD],
            source,
        )
    if REPOSITORY_FIELD in (schema.get("properties") or {}) or REPOSITORY_FIELD in (
        schema.get("required") or []
    ):
        report.error(
            "inputs.reserved",
            f"`{REPOSITORY_FIELD}` is reserved: it names the project's repository the work item works on "
            "(ADR 0124), which the project validates, not the workflow; name the field otherwise",
            ["metadata", "inputs", "properties", REPOSITORY_FIELD],
            source,
        )


def _check_conditions(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A transition chosen on the last stage's outputs (S48-01, ADR 0106).

    - Its `when` names outputs some transition of the workflow declares: a typo (`outcom`) would
      never match, and the work item would always take the default without saying why.
    - A state whose exits carry `when` keeps one exit without it, the default: when no condition
      holds (the agent answered something else, or nothing), the work item moves on instead of
      waiting forever.
    """
    declared = {name for t in wf.transitions for name in t.outputs}
    # A decision step writes its questions' answers and probabilities (ADR 0121): declared by its actor.
    declared |= {
        name
        for t in wf.transitions
        if isinstance(actor := wf.actor_of(t), DecisionActor)
        for name in actor.outputs
    }
    for index, t in enumerate(wf.transitions):
        for name in t.when or {}:
            if name not in declared:
                report.error(
                    "when.unknown_output",
                    f"`when` reads the output `{name}`, which no transition of this workflow declares in "
                    "`outputs`: it would never match",
                    ["transitions", index, "when", name],
                    source,
                )
    for state in wf.states:
        exits = wf.transitions_from(state)
        if any(t.when for t in exits) and all(t.when for t in exits):
            index = wf.transitions.index(next(t for t in exits if t.when))
            report.error(
                "when.no_default",
                f"every transition from `{state}` has a `when`: add one without it, taken when no condition "
                "holds, or a work item whose outputs match none would wait forever",
                ["transitions", index, "when"],
                source,
            )


def _check_decisions(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A decision step (ADR 0121).

    - Its questions write `<name>` and `<name>_confidence`: a question named like another's
      probability would write the same output twice.

    What depends on the project — a gateway that serves `/v1/systemone`, a catalogue entry with the
    `decide` usage — is checked where the project is known: when the API saves the workflow
    (`decision.gateway_unsupported`, `decision.model_usage`) and again when the step runs.
    """
    for name, actor in wf.actors.items():
        if not isinstance(actor, DecisionActor):
            continue
        outputs = actor.outputs
        for twice in sorted({o for o in outputs if outputs.count(o) > 1}):
            report.error(
                "decision.output_collision",
                f"the decision `{name}` writes the output `{twice}` twice: a question is named like another "
                "question's probability",
                ["actors", name, "questions"],
                source,
            )


def _check_confidence_thresholds(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A branch on a probability keeps a person below the threshold (ADR 0121 §7) — a warning.

    A state whose exits read a `*_confidence` output in their `when` lets a probability choose the
    branch. Its default exit — the one without `when`, taken when the probability is below the
    threshold — hands the work item to a person when it is itself a human step, or when the state it
    leads to is left by a human step. Otherwise the work items the model was least sure of move on
    unchecked, and no person's verdict tells the ledger whether the decision was right:
    `decision.threshold_without_human`. A warning, not an error: a workflow may choose to let an
    unsure answer through. A state with no default exit is `when.no_default`'s, not this rule's.
    """
    for state in wf.states:
        exits = wf.transitions_from(state)
        reading = next(
            ((t, name) for t in exits for name in t.when or {} if name.endswith(CONFIDENCE_SUFFIX)), None
        )
        default = next((t for t in exits if not t.when), None)
        if reading is None or default is None or _hands_to_a_person(wf, default):
            continue
        transition, name = reading
        report.warn(
            "decision.threshold_without_human",
            f"`{state}` branches on `{name}`, and its default exit `{default.key}` goes to `{default.to}` "
            "with no person: a work item the model was unsure of moves on unchecked, and no human verdict "
            "measures the decision (ADR 0121) — make the default exit a human step, or lead it to one",
            ["transitions", wf.transitions.index(transition), "when", name],
            source,
        )


def _hands_to_a_person(wf: Workflow, transition: Transition) -> bool:
    """The transition is a human step, or the state it leads to is left by one."""
    if isinstance(wf.actor_of(transition), HumanActor):
        return True
    return any(isinstance(wf.actor_of(t), HumanActor) for t in wf.transitions_from(transition.to))


def _check_already_fixed_exits(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """`already_fixed_proven` (S22-47) guards the platform's "already fixed" exit: a `system`
    transition from the state an agent or job step starts from. It is taken only after that step
    measured no change; anywhere else it would never be taken, and its guarantee never read."""
    for index, t in enumerate(wf.transitions):
        for g_index, g in enumerate(t.gates):
            if g.name != ALREADY_FIXED_GATE:
                continue
            system = isinstance(wf.actors.get(t.by or ""), SystemActor)
            step = any(
                isinstance(wf.actors.get(other.by or ""), RUN_ACTORS)
                for other in wf.transitions_from(t.from_)
            )
            if not (system and step):
                report.error(
                    "gate.already_fixed_not_an_exit",
                    f"guarantee `{ALREADY_FIXED_GATE}` guards a `system` transition from the state an agent "
                    "step starts from: it is taken only when that step changed nothing",
                    ["transitions", index, "gates", g_index],
                    source,
                )


def _check_actions(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A governed action (S20-05) is proposed by a transition of the PLATFORM, its date is read
    from a field the workflow declares as a date, and `action_succeeded` only makes sense with
    an action to judge."""
    declared_fields = dict((wf.metadata.inputs or {}).get("properties") or {})
    for index, t in enumerate(wf.transitions):
        location: list[str | int] = ["transitions", index]
        if t.action is None:
            for g_index, g in enumerate(t.gates):
                if g.name == "action_succeeded":
                    report.error(
                        "gate.nothing_to_check",
                        "guarantee `action_succeeded` without `action:`: there is no action to judge",
                        [*location, "gates", g_index],
                        source,
                    )
            continue
        if t.on_fail is None:
            report.error(
                "action.on_fail_missing",
                "an action can fail or be rejected: `on_fail: {to, max_attempts, escalate_to}` says where "
                "the work item goes — without it, it would propose the same action forever",
                [*location, "action"],
                source,
            )
        actor = wf.actors.get(t.by) if t.by else None
        if not isinstance(actor, SystemActor):
            report.error(
                "action.not_system",
                "only a platform transition (`by:` a `system` actor) proposes an action: "
                "an agent calls tools, a person decides",
                [*location, "action"],
                source,
            )
        if t.action.not_before is None:
            continue
        field_name = field_of(t.action.not_before)
        declare = declared_fields.get(field_name)
        if not isinstance(declare, dict):
            report.error(
                "action.date_field_unknown",
                f"`not_before` reads `fields.{field_name}`, which `metadata.inputs` does not declare",
                [*location, "action", "not_before"],
                source,
            )
        elif declare.get("format") not in {"date", "date-time"}:
            report.error(
                "action.date_field_not_date",
                f"`fields.{field_name}` is not declared as a date (`format: date` or `date-time`)",
                [*location, "action", "not_before"],
                source,
            )


def _check_spawns(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A fan-out (S38-12, ADR 0057) is the platform's: a `system` transition that does nothing else.
    Its list is a declared field when it walks `fields.`, and a join that can fail says where the
    work item goes."""
    declared_fields = dict((wf.metadata.inputs or {}).get("properties") or {})
    for index, t in enumerate(wf.transitions):
        if t.spawn is None:
            continue
        location: list[str | int] = ["transitions", index, "spawn"]
        if not isinstance(wf.actors.get(t.by) if t.by else None, SystemActor):
            report.error(
                "spawn.not_system",
                "only a platform transition (`by:` a `system` actor) opens child work items",
                location,
                source,
            )
        if t.action is not None or t.does is not None:
            report.error(
                "spawn.not_alone",
                "a transition that opens children does nothing else: no `action:`, no `does:`",
                location,
                source,
            )
        root, _, rest = t.spawn.for_each.partition(".")
        field_name = rest.split(".", 1)[0]
        if root == "fields" and declared_fields and field_name not in declared_fields:
            report.error(
                "spawn.field_unknown",
                f"`for_each` reads `fields.{field_name}`, which `metadata.inputs` does not declare",
                [*location, "for_each"],
                source,
            )
        if t.spawn.min_succeeded and not t.spawn.succeeded:
            report.error(
                "spawn.succeeded_missing",
                "`min_succeeded` counts the children ending in a `succeeded` state: list those states",
                [*location, "min_succeeded"],
                source,
            )
        if t.on_fail is None:  # a list refused, a child workflow absent, too few successes
            report.error(
                "spawn.on_fail_missing",
                "a fan-out can fail: `on_fail: {to, max_attempts, escalate_to}` says where the item goes",
                location,
                source,
            )


def _check_tasks(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A task (S20-06) is done by a PERSON; its form is an object JSON Schema, and
    each of its properties is a field the workflow declares: the values are poured into it, and
    a following action reads them."""
    declared_fields = dict((wf.metadata.inputs or {}).get("properties") or {})
    for index, t in enumerate(wf.transitions):
        if t.task is None:
            continue
        location: list[str | int] = ["transitions", index, "task"]
        if not isinstance(wf.actors.get(t.by) if t.by else None, HumanActor):
            report.error(
                "task.not_human",
                "a task is done by a person: `by:` a `human` actor",
                location,
                source,
            )
        import jsonschema

        try:
            jsonschema.Draft202012Validator.check_schema(t.task.form)
        except jsonschema.SchemaError as error:
            report.error("task.form_invalid", f"the form is not a JSON Schema: {error.message}",
                         [*location, "form"], source)  # fmt: skip
            continue
        properties = t.task.form.get("properties") or {}
        if t.task.form.get("type", "object") != "object" or not properties:
            report.error(
                "task.form_invalid",
                "the form describes an object, with at least one field to fill",
                [*location, "form"],
                source,
            )
            continue
        for field_name in properties:
            if field_name not in declared_fields:
                report.error(
                    "task.field_unknown",
                    f"the form fills `fields.{field_name}`, which `metadata.inputs` does not declare",
                    [*location, "form", "properties", field_name],
                    source,
                )


def _check_references(
    wf: Workflow, states: set[str], gates: set[str], report: ValidationReport, source: Any
) -> None:
    for index, t in enumerate(wf.transitions):
        _check_transition_references(wf, t, ["transitions", index], states, gates, report, source)
    for actor_id, actor in wf.actors.items():
        # Before 2026-09-25, this loop re-read the `actor` variable of the transitions loop:
        # the escalation of a human actor to an unknown one was never seen.
        if isinstance(actor, HumanActor) and actor.escalate_to and actor.escalate_to not in wf.actors:
            report.error(
                "actor.unknown",
                f"escalation to an unknown actor: {actor.escalate_to}",
                ["actors", actor_id, "escalate_to"],
                source,
            )
    _check_defaults_references(wf, states, report, source)


def _check_transition_references(
    wf: Workflow,
    t: Any,
    path: list[str | int],
    states: set[str],
    gates: set[str],
    report: ValidationReport,
    source: Any,
) -> None:
    def check_state(target: str | None, location: list[str | int], label: str) -> None:
        if target is not None and target not in states:
            report.error("state.unknown", f"{label}: {target}", location, source)

    if t.from_ != AGENT_WILDCARD:
        check_state(t.from_, [*path, "from"], "unknown source state")
    check_state(t.to, [*path, "to"], "unknown target state")
    check_state(t.on_reject, [*path, "on_reject"], "unknown state")
    if t.by is not None and t.by not in wf.actors:
        report.error("actor.unknown", f"unknown actor: {t.by}", [*path, "by"], source)
    for g_index, g in enumerate(t.gates):
        if g.name not in gates:
            report.error(
                "gate.unknown",
                f"unknown gate: {g.name} (known: {', '.join(sorted(gates))})",
                [*path, "gates", g_index],
                source,
            )
    for field_name in ("on_fail", "on_changes_requested"):
        retry = getattr(t, field_name)
        if retry is None:
            continue
        for attr in ("to", "escalate_to"):
            check_state(
                getattr(retry, attr), [*path, field_name, attr], f"unknown state in {field_name}.{attr}"
            )
    for a_index, actor_id in enumerate(t.review.agents if t.review and t.review.agents else []):
        if actor_id not in wf.actors:
            report.error(
                "actor.unknown",
                f"unknown reviewer: {actor_id}",
                [*path, "review", "agents", a_index],
                source,
            )
    actor = wf.actors.get(t.by) if t.by else None
    if isinstance(actor, AgentActor) and actor.model == "profile:":
        report.error("model.profile_empty", "empty model profile", ["actors", t.by, "model"], source)


def _check_defaults_references(wf: Workflow, states: set[str], report: ValidationReport, source: Any) -> None:
    exceeded = wf.budget.on_exceeded if wf.budget is not None else None
    if exceeded is not None and exceeded not in states:
        report.error(
            "state.unknown",
            f"unknown state in budget.on_exceeded: {exceeded}",
            ["budget", "on_exceeded"],
            source,
        )
    defaults = wf.defaults
    if not defaults:
        return
    if defaults.from_any_agent_state:
        for attr in ("on_question", "on_budget_exceeded", "on_timeout"):
            target = getattr(defaults.from_any_agent_state, attr)
            if target is not None and target not in states:
                report.error(
                    "state.unknown",
                    f"unknown state in defaults.from_any_agent_state.{attr}: {target}",
                    ["defaults", "from_any_agent_state", attr],
                    source,
                )
    abandon = defaults.needs_human.on_abandon if defaults.needs_human else None
    if abandon and abandon not in states:
        report.error(
            "state.unknown", f"unknown state: {abandon}", ["defaults", "needs_human", "on_abandon"], source
        )


def _check_initial_and_terminal(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """The initial state is the one the document declares (§1.4); it leads to a terminal state."""
    initial = wf.initial_state
    if initial not in wf.states:
        report.error(
            "workflow.initial_state_unknown",
            f"`initial: {initial}` names no declared state",
            ["initial"],
            source,
        )
        return
    if wf.states[initial].terminal:
        report.error(
            "workflow.initial_state_terminal",
            f"the initial state ({initial}) cannot be terminal: the work item would be born finished",
            ["states", initial],
            source,
        )
    if not wf.terminal_states():
        report.error("workflow.no_terminal_state", "no terminal state declared", ["states"], source)

    # A state reachable only through a failure path is legitimate (needs_human);
    # a state no transition reaches, other than the initial state, is a typo.
    targets = set(_all_targets(wf))
    for name in wf.states:
        if name != initial and name not in targets:
            report.error(
                "state.no_inbound",
                f"no transition leads to the state {name} (the initial state is {initial})",
                ["states", name],
                source,
            )


def _all_targets(wf: Workflow) -> list[str]:
    """All the declared target states, returns, escalations and defaults included."""
    targets: list[str] = []
    for t in wf.transitions:
        targets.append(t.to)
        if t.on_reject:
            targets.append(t.on_reject)
        for retry in (t.on_fail, t.on_changes_requested):
            if retry is not None:
                targets += [retry.to, retry.escalate_to]
    defaults = wf.defaults
    if defaults and defaults.from_any_agent_state:
        d = defaults.from_any_agent_state
        targets += [x for x in (d.on_question, d.on_budget_exceeded, d.on_timeout) if x]
    if wf.budget is not None and wf.budget.on_exceeded:
        targets.append(wf.budget.on_exceeded)
    if defaults and defaults.needs_human and defaults.needs_human.on_abandon:
        targets.append(defaults.needs_human.on_abandon)
    return targets


def _reachable_states(wf: Workflow) -> set[str]:
    agent_states = wf.agent_driven_states()
    seen = {wf.initial_state}
    stack = [wf.initial_state]
    while stack:
        current = stack.pop()
        for t in wf.transitions:
            if t.from_ != current and not (t.from_ == AGENT_WILDCARD and current in agent_states):
                continue
            targets = [t.to, t.on_reject]
            for retry in (t.on_fail, t.on_changes_requested):
                if retry is not None:
                    targets += [retry.to, retry.escalate_to]
            if wf.defaults and wf.defaults.from_any_agent_state and current in agent_states:
                d = wf.defaults.from_any_agent_state
                targets += [d.on_question, d.on_budget_exceeded, d.on_timeout]
            if wf.budget is not None and current in agent_states:
                targets.append(wf.budget.on_exceeded)
            for target in targets:
                if target and target in wf.states and target not in seen:
                    seen.add(target)
                    stack.append(target)
    if wf.defaults and wf.defaults.needs_human and wf.defaults.needs_human.on_abandon:
        target = wf.defaults.needs_human.on_abandon
        if target in wf.states:
            seen.add(target)
    return seen


def _check_reachability(wf: Workflow, report: ValidationReport, source: Any) -> None:
    reachable = _reachable_states(wf)
    for name in wf.states:
        if name not in reachable:
            report.error("state.orphan", f"orphan state (unreachable): {name}", ["states", name], source)
    terminals = set(wf.terminal_states())
    if terminals and not (terminals & reachable):
        report.error(
            "workflow.terminal_unreachable",
            "no terminal state is reachable from the initial state",
            ["states"],
            source,
        )


def _check_effects(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """What the platform does is WRITTEN (#175): `does` on a system transition, `production`
    on a state. An effect still deduced from a name is read (a published workflow does not change
    behaviour), but it announces itself: a rename would lose it."""
    gestures = {"open_pr": "opens the PR", "merge_pr": "merges the PR"}
    for index, t in enumerate(wf.transitions):
        is_system = isinstance(wf.actors.get(t.by or ""), SystemActor)
        if t.does is not None and not is_system:
            message = f"`does: {t.does}` is a platform gesture: only a `system` actor carries it"
            report.error("transition.does_requires_system", message, ["transitions", index, "does"], source)
        implicit = implicit_effect(t.to)
        if is_system and t.does is None and implicit is not None:
            message = (
                f"the transition to `{t.to}` {gestures[implicit]} because of the state's NAME: "
                f"write `does: {implicit}`, so that a rename no longer changes it"
            )
            report.warn("workflow.implicit_effect", message, ["transitions", index, "to"], source)
        if t.pull_request is not None and (not is_system or transition_effect(t) != "open_pr"):
            message = (
                "`pull_request:` says how the platform opens the pull request: only a `system` transition "
                "that opens it (`does: open_pr`) carries it"
            )
            report.error(
                "transition.pull_request_without_open_pr",
                message,
                ["transitions", index, "pull_request"],
                source,
            )
    for state_name, state in wf.states.items():
        if state_name.startswith(PRODUCTION_PREFIX) and not state.production:
            message = (
                f"the state `{state_name}` is production because of its NAME: write `production: true`, "
                "so that a rename no longer lifts the lock"
            )
            report.warn("workflow.implicit_effect", message, ["states", state_name], source)


def _check_prod_states(wf: Workflow, report: ValidationReport, source: Any) -> None:
    for index, t in enumerate(wf.transitions):
        if is_production_state(t.to, wf.states.get(t.to)) and t.via != "release_train":
            report.error(
                "prod.requires_train",
                f"the state {t.to} can only be reached `via: release_train` (production is locked)",
                ["transitions", index, "to"],
                source,
            )


def _check_retries(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """Every return transition carries a `max_attempts`: no infinite loop."""
    order = list(wf.states)
    position = {name: i for i, name in enumerate(order)}
    for index, t in enumerate(wf.transitions):
        if t.from_ == AGENT_WILDCARD or t.via == "release_train":
            continue
        backwards = position.get(t.to, 0) < position.get(t.from_, 0)
        if backwards and t.on_fail is None and t.on_changes_requested is None:
            actor = wf.actors.get(t.by) if t.by else None
            if isinstance(actor, RUN_ACTORS):
                report.error(
                    "transition.unbounded_retry",
                    f"return transition {t.from_} → {t.to} without `on_fail.max_attempts`",
                    ["transitions", index],
                    source,
                )


# What a guarantee needs to find on the transition to be able to pronounce.
# `outputs_present` without `outputs:` looks at nothing; neither does `evidence_facts` without `keys:`.
GATES_NEEDING_PARAMS: dict[str, tuple[str, str]] = {
    "outputs_present": ("outputs", "`outputs:` on the transition"),
    "evidence_facts": ("keys", "`keys:` as a parameter of the guarantee"),
    "tool_called": ("tools", "`tools:` as a parameter of the guarantee"),
    "outputs_in": ("values", "`values:` as a parameter of the guarantee"),
    "markdown_sections": ("paths", "`paths:` as a parameter of the guarantee"),
    "output_schema": ("schemas", "`schemas:` as a parameter of the guarantee"),
    "numbers_measured": ("sources", "`sources:` as a parameter of the guarantee"),
}


def _check_gates_have_something_to_check(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A guarantee that is asked for with nothing to check is refused when written.

    Otherwise it is discovered in flight, and in the worst way: the dashboard shows a green
    guarantee that nobody evaluated. The engine also refuses at the moment of ruling, but a
    wrong workflow must fail when it is written, not when a work item goes through it.
    """
    for index, t in enumerate(wf.transitions):
        for g_index, g in enumerate(t.gates):
            need = GATES_NEEDING_PARAMS.get(g.name)
            if need is None:
                continue
            param, hint = need
            if g.params.get("allow_empty") or g.params.get(param) or (param == "outputs" and t.outputs):
                continue
            report.error(
                "gate.nothing_to_check",
                f"guarantee `{g.name}` without {hint}: it would have nothing to check",
                ["transitions", index, "gates", g_index],
                source,
            )


def _check_output_schemas(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """An `output_schema` guarantee checks declared outputs against schemas that are schemas.

    A typo in a schema (`"type": "aray"`) would otherwise surface as a failed guarantee on the first
    item, and a schema for an output the transition does not declare would check nothing, forever.
    """
    for index, t in enumerate(wf.transitions):
        for g_index, g in enumerate(t.gates):
            if g.name != "output_schema":
                continue
            path: list[str | int] = ["transitions", index, "gates", g_index, "params", "schemas"]
            schemas = g.params.get("schemas")
            if schemas is None:
                continue  # `gate.nothing_to_check` says it
            if not isinstance(schemas, dict):
                report.error(
                    "gate.output_schema", "`schemas:` maps an output's name to its JSON Schema", path, source
                )
                continue
            for name, schema in schemas.items():
                if name not in t.outputs:
                    report.error(
                        "gate.output_schema",
                        f"schema for `{name}`, which the transition does not declare in `outputs:`",
                        [*path, name],
                        source,
                    )
                try:
                    Draft202012Validator.check_schema(schema)
                except SchemaError as exc:
                    report.error(
                        "gate.output_schema",
                        f"the schema of `{name}` is not a JSON Schema: {exc.message[:200]}",
                        [*path, name],
                        source,
                    )


def _check_known_roles(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A role the package does not know requires a playbook brought by the deployment.

    This is not an error: it is even the point, since a trade names its roles. But the
    playbook is resolved BY THE ROLE'S NAME, and its absence would only be found at the first
    work item, on an unknown-playbook error in the middle of a stage. A warning when writing
    costs less.
    """
    try:
        from choregos_playbooks import playbook_path
    except Exception:  # the validator must run without the playbooks package
        return
    for actor_id, actor in wf.actors.items():
        if not isinstance(actor, AgentActor):
            continue
        if actor.agent:
            # A registry agent brings its instructions (ADR 0033): the playbook is only
            # a fallback, and warning about it would be a warning people learn to ignore.
            continue
        role = str(actor.playbook or actor.role)
        try:
            # We try the RESOLUTION, not a comparison with the package's list: a trade
            # role has its playbook in the deployment (`CHOREGOS_PLAYBOOKS_DIR`), and
            # warning when it is there would be a warning people learn to ignore.
            playbook_path(role)
        except FileNotFoundError:
            report.warn(
                "role.playbook_not_found",
                f"no playbook for the role `{role}`: the deployment must provide "
                f"`{role}.md` (CHOREGOS_PLAYBOOKS_DIR)",
                ["actors", actor_id, "role"],
                source,
            )


def _check_unenforced_review(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """A review the platform would never run is refused, not accepted silently (ADR 0101).

    `review.humans` has no reader: nobody is asked. A `review.agents` entry other than the
    transition's `by` actor never runs either: only `by` runs on a transition.
    """
    for index, t in enumerate(wf.transitions):
        if t.review is None:
            continue
        if t.review.humans and t.review.humans.required != "never":
            report.error(
                "review.humans_not_enforced",
                "`review.humans` is not enforced: Choregos asks no one for this review. "
                "Declare a human actor and a transition it answers (ADR 0044).",
                ["transitions", index, "review", "humans"],
                source,
            )
        for agent in t.review.agents:
            if agent == t.by:
                continue
            report.error(
                "review.agent_not_run",
                f"`{agent}` is named in `review.agents` of `{t.id}` but never runs: only the "
                "transition's `by` actor runs. Give it its own transition.",
                ["transitions", index, "review", "agents"],
                source,
            )


def _check_warnings(wf: Workflow, report: ValidationReport, source: Any) -> None:
    if not any(name == "needs_human" or state.kind == "wait" for name, state in wf.states.items()):
        report.warn(
            "workflow.no_human_state",
            "no human waiting state: the workflow can never ask a person to arbitrate",
            ["states"],
            source,
        )
    roles = {str(actor.role) for actor in wf.actors.values() if isinstance(actor, AgentActor)}
    used_roles: set[str] = set()
    for t in wf.transitions:
        actor = wf.actors.get(t.by) if t.by else None
        if isinstance(actor, AgentActor):
            used_roles.add(str(actor.role))
    has_verify = "verify" in (roles & used_roles)
    # Without an agent that writes code, nothing to test before the PR: a study that merges an ADR was
    # warned about having no `verify` (S21-22).
    writes_code = "implement" in (roles & used_roles)
    has_pr = any(
        isinstance(wf.actors.get(t.by or ""), SystemActor) and transition_effect(t) == "open_pr"
        for t in wf.transitions
    )
    if has_pr and writes_code and not has_verify:
        report.warn(
            "workflow.no_verify_before_pr",
            "no `verify` step before the pull request is opened",
            ["transitions"],
            source,
        )
    train_approvers = {str(t.train.approval) for t in wf.transitions if t.train and t.train.approval}
    for actor_id, declared in wf.actors.items():
        if isinstance(declared, HumanActor) and declared.group is None and actor_id in train_approvers:
            report.error(
                "actor.train_approver_group",
                f"human actor `{actor_id}` approves a train: it names a `group` (a train's departure is "
                "approved by a group; a `user` alone is for a workflow step, ADR 0153)",
                ["actors", actor_id],
                source,
            )
        if isinstance(declared, HumanActor) and declared.group is not None:
            refused = placeholder_error(declared.group)
            if refused is not None:
                report.error("actor.group_placeholder", refused, ["actors", actor_id, "group"], source)
            elif is_per_project(declared.group) and actor_id in train_approvers:
                report.error(
                    "actor.group_placeholder",
                    f"human actor `{actor_id}` approves a train: its group cannot name `{{project}}` "
                    "(a train's approval group is the release train's, not a project step's)",
                    ["actors", actor_id, "group"],
                    source,
                )
        if isinstance(declared, HumanActor) and declared.user is None and not (declared.group or "").strip():
            report.warn(
                "actor.group_empty",
                f"human actor `{actor_id}` names no group: nobody can answer its steps but an "
                "organisation administrator",
                ["actors", actor_id, "group"],
                source,
            )
    for actor_id in wf.actors:
        used = any(t.by == actor_id for t in wf.transitions)
        used = used or any(t.train and t.train.approval == actor_id for t in wf.transitions)
        if not used:
            report.warn(
                "actor.unused",
                f"actor declared but never used: {actor_id}",
                ["actors", actor_id],
                source,
            )
