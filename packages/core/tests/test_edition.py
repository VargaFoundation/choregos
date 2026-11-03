"""The typed edits of a workflow, grafted into the text (ADR 0031, S16-11).

The property that matters: an operation followed by its inverse gives the original bytes back, on
a document that mixes styles (flow, block, quotes, comments everywhere, no final line break)
as on the shipped templates. And an operation does not touch what it does not target:
the comments stay.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_core import parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, template_yaml
from choregos_core.dsl.edition import EditRefused, edit

MIXED = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: arrival, version: 3}  # the current version
# Who acts
actors:
  hr: {type: human, group: hr, sla_hours: 24}
  # the coordinator
  coordinator:
    type: agent
    role: refine
  platform: {type: system}
  former: {type: human, group: archives}
states:
  # the request arrives here
  request: {display: Request, kind: wait}
  "preparation":
    display: "Preparation"   # in progress
    kind: work
  badge_wait: {display: 'Badge to place', kind: wait}
  orphan: {display: Unused}
  done:
    display: Done
    terminal: true
transitions:
  - {id: t-prepare, from: request, to: preparation, by: coordinator, gates: [outputs_present]}
  # HR validates
  - id: t-validate
    from: "preparation"
    to: badge_wait
    by: hr
    timeout_hours: 48
    gates:
      - review_approved
      - {name: evidence_present, params: {kind: photo}}
  - {id: t-badge, from: badge_wait, to: done, by: platform}
defaults:
  from_any_agent_state: {on_question: request, on_timeout: request}"""

#: The transitions last, with no final line break: removing or adding the last touches the end of the file.
TRANSITIONS_LAST = MIXED[: MIXED.index("defaults:")].rstrip("\n")

DOCUMENTS = {
    "mixed": MIXED,
    "mixed-final-line-break": MIXED + "\n",
    "transitions-last": TRANSITIONS_LAST,
    **{f"template-{name}": template_yaml(name) for name in TEMPLATE_NAMES},
}


#: A human task's form (S20-06), the kind of nested value the map writes in block style (ADR 0112 §3).
TASK = {
    "title": "Hand over the badge",
    "instructions": "Give the joiner their badge.",
    "form": {"type": "object", "required": ["badge"], "properties": {"badge": {"type": "string"}}},
    "attest": "I handed the badge over in person.",
}


def _comments(text: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r"#[^\n]*", text)]


def _operations(text: str) -> list[dict[str, Any]]:
    """All the operations that can be tried on a document."""
    document = yaml.safe_load(text)
    states = list(document["states"])
    actors = list(document["actors"])
    transitions = [t for t in document["transitions"] if t.get("id")]
    ops: list[dict[str, Any]] = []
    ops += [
        {"op": "set_workflow", "field": "description", "value": "A new description"},
        {"op": "set_workflow", "field": "initial", "value": states[0]},
        {"op": "set_workflow", "field": "initial", "unset": True},
        {
            "op": "set_workflow",
            "field": "inputs",
            "value": {"type": "object", "properties": {"start": {"type": "string", "format": "date"}}},
            "style": "block",
        },
    ]
    for place in (None, 0, 1):
        ops.append({"op": "add_state", "name": "new_state", "spec": {"kind": "wait"}, "index": place})
        ops.append({"op": "add_actor", "name": "new_actor", "spec": {"type": "system"}, "index": place})
        ops.append(
            {
                "op": "add_transition",
                "transition": {"from": states[0], "to": states[-1], "by": actors[0]},
                "index": place,
            }
        )
    for state in states:
        ops += [
            {"op": "remove_state", "name": state},
            {"op": "rename_state", "from": state, "to": "renamed"},
            {"op": "set_state", "name": state, "field": "display", "value": "New label"},
            {"op": "set_state", "name": state, "field": "tracker", "value": {"status": "In progress"}},
            {"op": "set_state", "name": state, "field": "kind", "unset": True},
        ]
    for actor in actors:
        ops += [
            {"op": "remove_actor", "name": actor},
            {"op": "set_actor", "name": actor, "field": "sla_hours", "value": 12},
        ]
    for transition in transitions:
        ident = transition["id"]
        ops += [
            {"op": "remove_transition", "id": ident},
            {"op": "set_transition", "id": ident, "field": "timeout_hours", "value": 72},
            {"op": "set_transition", "id": ident, "field": "by", "value": actors[-1]},
            {"op": "set_transition", "id": ident, "field": "to", "unset": True},
            {"op": "add_gate", "transition": ident, "gate": "scope_respected"},
            {"op": "set_transition", "id": ident, "field": "gates", "value": ["outputs_present"]},
            {"op": "set_transition", "id": ident, "field": "task", "value": TASK, "style": "block"},
            {
                "op": "set_transition",
                "id": ident,
                "field": "gates",
                "value": ["outputs_present"],
                "style": "block",
            },
            {
                "op": "add_gate",
                "transition": ident,
                "gate": {"name": "diff_size_max", "params": {"files": 9}},
                "index": 0,
            },
        ]
        for guarantee in transition.get("gates") or []:
            name = guarantee if isinstance(guarantee, str) else guarantee["name"]
            ops.append({"op": "remove_gate", "transition": ident, "name": name})
    return ops


def _cases() -> list[Any]:
    cases = []
    for name, text in DOCUMENTS.items():
        for i, op in enumerate(_operations(text)):
            cases.append(pytest.param(text, op, id=f"{name}-{op['op']}-{i}"))
    return cases


@pytest.mark.parametrize(("text", "op"), _cases())
def test_an_operation_then_its_inverse_give_the_bytes_back(text: str, op: dict[str, Any]) -> None:
    try:
        edition = edit(text, [op])
    except EditRefused:
        return  # refused: nothing changed, another test says when to refuse
    yaml.safe_load(edition.yaml)  # the edited text is YAML
    inverse = [o.model_dump(by_alias=True, exclude_none=True) for o in edition.inverse]
    assert edit(edition.yaml, inverse).yaml == text
    if not op["op"].startswith("remove") and not op.get("unset"):
        assert _comments(text) == [c for c in _comments(edition.yaml) if c in _comments(text)]


def test_each_kind_of_operation_applies_somewhere() -> None:
    """The property above is worth nothing if everything is refused: each kind must pass once."""
    seen: set[str] = set()
    for text in DOCUMENTS.values():
        for op in _operations(text):
            try:
                edit(text, [op])
            except EditRefused:
                continue
            seen.add(op["op"] + (":unset" if op.get("unset") else ""))
    assert seen >= {
        "add_state",
        "remove_state",
        "rename_state",
        "set_state",
        "set_state:unset",
        "add_transition",
        "remove_transition",
        "set_transition",
        "set_transition:unset",
        "add_gate",
        "remove_gate",
        "add_actor",
        "remove_actor",
        "set_actor",
    }, seen


def _errors(text: str) -> set[tuple[str, str | None]]:
    """The validator's errors, by kind and by path: an edit adds none."""
    return {(i.code, i.path) for i in parse_workflow(text, strict=False)[1].errors}


def test_renaming_a_state_follows_each_reference() -> None:
    edition = edit(MIXED, [{"op": "rename_state", "from": "request", "to": "reception"}])
    assert _errors(edition.yaml) == _errors(MIXED)
    workflow, _ = parse_workflow(edition.yaml, strict=False)
    assert "request" not in workflow.states and "reception" in workflow.states
    assert workflow.transitions[0].from_ == "reception"
    assert workflow.defaults is not None and workflow.defaults.from_any_agent_state is not None
    assert workflow.defaults.from_any_agent_state.on_question == "reception"
    # A name written between quotes stays quoted.
    renamed = edit(MIXED, [{"op": "rename_state", "from": "preparation", "to": "instruction"}]).yaml
    assert '"instruction":' in renamed and 'from: "instruction"' in renamed


def test_an_added_guarantee_keeps_the_style_of_its_list() -> None:
    flow = edit(MIXED, [{"op": "add_gate", "transition": "t-prepare", "gate": "scope_respected"}]).yaml
    assert "gates: [outputs_present, scope_respected]" in flow
    block = edit(MIXED, [{"op": "add_gate", "transition": "t-validate", "gate": "scope_respected"}]).yaml
    assert "      - {name: evidence_present, params: {kind: photo}}\n      - scope_respected\n" in block
    bare = edit(MIXED, [{"op": "add_gate", "transition": "t-badge", "gate": "ci_green"}]).yaml
    assert "{id: t-badge, from: badge_wait, to: done, by: platform, gates: [ci_green]}" in bare
    for text in (flow, block, bare):
        assert _errors(text) == _errors(MIXED)


@pytest.mark.parametrize(
    ("op", "reason"),
    [
        pytest.param({"op": "remove_state", "name": "request"}, "still named", id="state-reference"),
        pytest.param({"op": "remove_actor", "name": "hr"}, "still carries", id="actor-reference"),
        pytest.param(
            {"op": "rename_state", "from": "request", "to": "done"}, "already", id="rename-to-an-existing-one"
        ),
        pytest.param(
            {"op": "rename_state", "from": "request", "to": "Not A Name"}, "state name", id="invalid-name"
        ),
        pytest.param({"op": "add_state", "name": "request"}, "already exists", id="existing-state"),
        pytest.param(
            {"op": "remove_transition", "id": "t-unknown"}, "does not exist", id="unknown-transition"
        ),
        pytest.param(
            {"op": "set_workflow", "field": "name", "value": "other"},
            "the name is the route's",
            id="workflow-name",
        ),
        pytest.param(
            {"op": "set_transition", "id": "t-validate", "field": "id", "value": "x"}, "id", id="id"
        ),
        pytest.param(
            {"op": "add_gate", "transition": "t-validate", "gate": "review_approved"},
            "already",
            id="duplicate-guarantee",
        ),
        # `on_timeout` names `later`, which is not (yet) a state: the inverse rename would
        # rename it too, and would not give the original bytes back.
        pytest.param(
            {"op": "rename_state", "from": "badge_wait", "to": "later"},
            "already named",
            id="rename-to-an-already-cited-name",
        ),
        pytest.param(
            {
                "op": "add_transition",
                "transition": {"id": "t-prepare", "from": "request", "to": "done", "by": "hr"},
            },
            "already exists",
            id="existing-transition",
        ),
    ],
)
def test_what_is_refused(op: dict[str, Any], reason: str) -> None:
    text = MIXED.replace("on_timeout: request}", "on_timeout: later}")
    with pytest.raises(EditRefused, match=reason):
        edit(text, [op])


def test_the_first_field_of_a_block_transition_does_not_move() -> None:
    """It shares the dash's line: removing it or inserting before it would break the sequence."""
    text = MIXED.replace(
        '  - id: t-validate\n    from: "preparation"', '  - from: "preparation"\n    id: t-validate'
    )
    assert '  - from: "preparation"\n    id: t-validate' in text
    with pytest.raises(EditRefused, match="dash's line"):
        edit(text, [{"op": "set_transition", "id": "t-validate", "field": "from", "unset": True}])
    # The rest of the transition can be modified.
    edition = edit(
        text, [{"op": "set_transition", "id": "t-validate", "field": "timeout_hours", "unset": True}]
    )
    inverse = [o.model_dump(by_alias=True, exclude_none=True) for o in edition.inverse]
    assert edit(edition.yaml, inverse).yaml == text


#: A workflow written BEFORE #175: its effects are in the names (`pr_open`, `merged`, `deployed_prod`).
FORMER = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: former, version: 1 }
actors:
  ci: { type: system }
  captain: { type: human, group: release-captains, sla_hours: 4 }
  owner: { type: human, group: product-owners, sla_hours: 24 }
states:
  inbox: { display: To sort, kind: wait }
  in_progress: { display: In progress }
  pr_open: { display: PR open, kind: wait }
  merged: { display: Merged }
  deployed_prod: { display: In production, terminal: true }
transitions:
  - { id: t-start, from: inbox, to: in_progress, by: owner }
  - { id: t-pr, from: in_progress, to: pr_open, by: ci }
  - { id: t-merge, from: pr_open, to: merged, by: ci }
  - id: t-deploy
    from: merged
    to: deployed_prod
    via: release_train
    train: { env: prod, approval: captain }
"""


def test_renaming_a_state_with_an_effect_writes_its_effect_first() -> None:
    """#175: `pr_open` opened the PR by its NAME. Renamed from the console, it would have lost it without
    saying so. The rename first writes `does: open_pr`: the workflow does the same thing after."""
    from choregos_contracts.workflow import transition_effect

    edition = edit(FORMER, [{"op": "rename_state", "from": "pr_open", "to": "review"}])
    after, _ = parse_workflow(edition.yaml, strict=False)
    (t_pr,) = [t for t in after.transitions if t.id == "t-pr"]
    assert (t_pr.to, t_pr.does, transition_effect(t_pr)) == ("review", "open_pr", "open_pr")
    assert any("is now written" in a for a in edition.warnings)
    assert edit(edition.yaml, edition.inverse).yaml == FORMER, "the inverse undoes the whole, to the byte"


def test_renaming_production_does_not_lift_the_lock() -> None:
    edition = edit(FORMER, [{"op": "rename_state", "from": "deployed_prod", "to": "in_service"}])
    after, report = parse_workflow(edition.yaml, strict=False)
    assert after.states["in_service"].production is True
    assert report.valid, [e.message for e in report.errors]
    # The lock holds: a transition that leads there without a train is refused, whatever the name.
    without_train = edition.yaml.replace(
        "    via: release_train\n    train: { env: prod, approval: captain }\n", "    by: captain\n"
    )
    _, refusal = parse_workflow(without_train, strict=False)
    assert any(e.code == "prod.requires_train" for e in refusal.errors)


def test_a_written_effect_is_read_and_a_deduced_effect_announces_itself() -> None:
    _, former = parse_workflow(FORMER, strict=False)
    assert len([w for w in former.warnings if w.code == "workflow.implicit_effect"]) == 3
    new = edit(
        FORMER,
        [
            {"op": "set_transition", "id": "t-pr", "field": "does", "value": "open_pr"},
            {"op": "set_transition", "id": "t-merge", "field": "does", "value": "merge_pr"},
            {"op": "set_state", "name": "deployed_prod", "field": "production", "value": True},
        ],
    ).yaml
    _, written = parse_workflow(new, strict=False)
    assert [w for w in written.warnings if w.code == "workflow.implicit_effect"] == []


def test_does_is_only_a_platform_gesture() -> None:
    human = edit(FORMER, [{"op": "set_transition", "id": "t-start", "field": "does", "value": "open_pr"}])
    _, report = parse_workflow(human.yaml, strict=False)
    assert any(e.code == "transition.does_requires_system" for e in report.errors)


def test_an_effect_without_a_transition_id_is_not_renamed_in_silence() -> None:
    without_id = FORMER.replace(
        "  - { id: t-pr, from: in_progress, to: pr_open, by: ci }",
        "  - { from: in_progress, to: pr_open, by: ci }",
    )
    with pytest.raises(EditRefused, match="has no `id`"):
        edit(without_id, [{"op": "rename_state", "from": "pr_open", "to": "review"}])


def test_a_written_effect_keeps_what_the_validator_deduces_from_it() -> None:
    """After the rename, the PR still opens: the validator knows it (`no_verify_before_pr`, which it
    only says of a workflow where an agent writes code, S21-22)."""
    with_a_dev = FORMER.replace(
        "  owner: { type: human, group: product-owners, sla_hours: 24 }\n",
        "  owner: { type: human, group: product-owners, sla_hours: 24 }\n"
        '  dev: { type: agent, role: implement, model: "profile:standard" }\n',
    ).replace("to: in_progress, by: owner }", "to: in_progress, by: dev }")
    _, before = parse_workflow(with_a_dev, strict=False)
    renamed = edit(with_a_dev, [{"op": "rename_state", "from": "pr_open", "to": "review"}]).yaml
    _, after = parse_workflow(renamed, strict=False)
    for report in (before, after):
        assert any(w.code == "workflow.no_verify_before_pr" for w in report.warnings)


def test_renaming_to_a_name_with_an_effect_announces_it() -> None:
    """A name starting with `pr_`, `merged` or `deployed_prod` still reads as an effect: giving it says so."""
    edition = edit(FORMER, [{"op": "rename_state", "from": "in_progress", "to": "pr_draft"}])
    assert any("carries an effect through its name" in a for a in edition.warnings)


def test_several_operations_undo_in_the_opposite_order() -> None:
    operations = [
        {"op": "add_state", "name": "checking", "spec": {"kind": "work"}},
        {
            "op": "add_transition",
            "transition": {"id": "t-check", "from": "badge_wait", "to": "checking", "by": "hr"},
        },
        {"op": "rename_state", "from": "checking", "to": "control"},
        {"op": "add_gate", "transition": "t-check", "gate": "evidence_present"},
        {"op": "add_gate", "transition": "t-prepare", "gate": "no_secrets", "index": 0},
    ]
    edition = edit(MIXED, operations)
    assert "control" in yaml.safe_load(edition.yaml)["states"]
    inverse = [o.model_dump(by_alias=True, exclude_none=True) for o in edition.inverse]
    assert edit(edition.yaml, inverse).yaml == MIXED


# The console (S16-12) emits these operations: each of its gestures must pass the core's door. The same
# file is read by `apps/web/tests/workflow-edition.test.tsx`, which checks that the console emits them.
# The web fixture owns these keys and its file name (S45-29).  # lang: fr-ok
GESTURES = json.loads(
    (Path(__file__).parents[3] / "apps/web/tests/fixtures/gestes-de-la-console.json").read_text("utf-8")
)


@pytest.mark.parametrize(
    "gesture", GESTURES["gestes"], ids=[f"{g['geste']}-{i}" for i, g in enumerate(GESTURES["gestes"])]
)
def test_each_console_gesture_grafts_and_undoes(gesture: dict[str, Any]) -> None:
    text = GESTURES["workflow"]
    edition = edit(text, [gesture["operation"]])
    # Grafted, not necessarily valid (removing the only transition to a state leaves it orphaned):
    # the API reports validity next to the text, without refusing the gesture.
    assert edition.yaml != text
    assert isinstance(yaml.safe_load(edition.yaml), dict)
    inverse = [o.model_dump(by_alias=True, exclude_none=True) for o in edition.inverse]
    assert edit(edition.yaml, inverse).yaml == text


def test_a_block_value_is_written_one_field_per_line_under_the_transition() -> None:
    """ADR 0112 §3: a reviewer reads a human task as lines, not as one flow line."""
    text = template_yaml("default-request")
    fields = {"type": "object", "properties": {"badge": {"type": "string"}}}
    edition = edit(
        text,
        [
            {"op": "set_workflow", "field": "inputs", "value": fields, "style": "block"},
            {"op": "set_transition", "id": "t-accept", "field": "task", "value": TASK, "style": "block"},
        ],
    )
    added = [line for line in edition.yaml.splitlines() if line not in text.splitlines()]
    assert "    task:" in added and "  inputs:" in added, added
    assert "      title: Hand over the badge" in added
    assert all(len(line) < 110 for line in added)
    (accept,) = [t for t in yaml.safe_load(edition.yaml)["transitions"] if t["id"] == "t-accept"]
    assert accept["task"] == TASK
    assert parse_workflow(edition.yaml)[1].valid
    assert edit(edition.yaml, edition.inverse).yaml == text


def test_a_value_written_in_block_style_can_be_replaced() -> None:
    """It was refused before ADR 0112: the whole key's lines are replaced, and the inverse puts them back."""
    text = template_yaml("default-request")
    with_task = edit(
        text, [{"op": "set_transition", "id": "t-accept", "field": "task", "value": TASK, "style": "block"}]
    ).yaml
    other = {**TASK, "title": "Return the laptop"}
    edition = edit(
        with_task,
        [{"op": "set_transition", "id": "t-accept", "field": "task", "value": other, "style": "block"}],
    )
    (accept,) = [t for t in yaml.safe_load(edition.yaml)["transitions"] if t["id"] == "t-accept"]
    assert accept["task"]["title"] == "Return the laptop"
    assert edit(edition.yaml, edition.inverse).yaml == with_task


def test_set_workflow_writes_initial_after_metadata_and_the_fields_in_metadata() -> None:
    text = template_yaml("default-request")
    edition = edit(
        text,
        [
            {"op": "set_workflow", "field": "initial", "value": "refining"},
            {"op": "set_workflow", "field": "description", "value": "Requests of the HR desk."},
        ],
    )
    lines = edition.yaml.splitlines()
    assert lines[lines.index("actors:") - 1] == "initial: refining", "right after metadata"
    document = yaml.safe_load(edition.yaml)
    assert document["metadata"]["description"] == "Requests of the HR desk."
    assert _comments(edition.yaml) == _comments(text)
    assert edit(edition.yaml, edition.inverse).yaml == text
