"""The shipped workflows hold their diff guarantees to the merge (S65-07, ADR 0171).

A merge names the commit its own transition's gates judged: a guarantee on the diff that only an
earlier step carries is not held on what is merged. Every workflow the repository ships that merges
therefore declares, on its merge transition, `no_secrets` and each guarantee an earlier step asks of
the work item's whole diff, with the same parameters.

`scope_respected` has two readings, and its declaration says which. Without `over` it judges what a
run wrote: on a transition the platform takes, the run is the last step's, or none. With `over:
work_item` it judges the work item's whole diff and nothing of a run: the paths the policy forbids,
and the `paths` the transition declares. The shipped merge transitions declare the second.

The validator warns about a merge transition that reads no diff (`workflow.no_diff_gate_at_merge`)
and about a run's reading on a transition the platform takes (`workflow.run_scope_on_system_step`),
and refuses an `over` it does not know (`gate.scope_over_unknown`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import SystemActor
from choregos_contracts.workflow import Transition, Workflow, transition_effect
from choregos_core import parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, template_yaml
from choregos_core.dsl.process import gate_detail, gate_summary, to_process
from choregos_core.gates import DIFF_GATES, OVER_WORK_ITEM, GateContext, evaluate, judged_on_a_run

ROOT = Path(__file__).resolve().parents[3]
MERGE_WARNING = "workflow.no_diff_gate_at_merge"
RUN_SCOPE_WARNING = "workflow.run_scope_on_system_step"


def _merge_of(workflow: Workflow) -> Transition | None:
    for transition in workflow.transitions:
        if (
            isinstance(workflow.actor_of(transition), SystemActor)
            and transition_effect(transition) == "merge_pr"
        ):
            return transition
    return None


def _every_shipped_workflow() -> dict[str, tuple[Workflow, list[str]]]:
    """Every workflow the repository ships — the core's and those of `templates/` — with the codes of
    the validator's warnings."""
    texts = {f"core/{name}": template_yaml(name) for name in TEMPLATE_NAMES}
    for path in sorted((ROOT / "templates").glob("*/workflows/*.yaml")):
        texts[f"{path.parents[1].name}/{path.stem}"] = path.read_text(encoding="utf-8")
    found = {}
    for label, text in texts.items():
        workflow, report = parse_workflow(text, strict=True)
        found[label] = (workflow, [warning.code for warning in report.warnings])
    return found


EVERY = _every_shipped_workflow()
#: Those that merge, with their merge transition.
SHIPPED: dict[str, tuple[Workflow, Transition]] = {
    label: (workflow, merge)
    for label, (workflow, _) in EVERY.items()
    if (merge := _merge_of(workflow)) is not None
}


def _covers(held: Any, asked: Any) -> bool:
    """Whether the parameters declared at the merge ask at least what the earlier step asked."""
    if isinstance(asked, dict):
        return isinstance(held, dict) and all(_covers(held.get(key), value) for key, value in asked.items())
    if isinstance(asked, list):
        return isinstance(held, list) and all(value in held for value in asked)
    return bool(held == asked)


def test_the_shipped_workflows_that_merge_are_all_read() -> None:
    assert len(SHIPPED) >= 15, sorted(SHIPPED)
    assert {
        "core/default-simple",
        "core/full-auto",
        "core/advanced",
        "core/dev-feature",
        "core/documentation",
        "core/architecture-decision",
        "github-software-delivery/dev-simple",
        "github-software-delivery/dev-complex",
        "github-software-delivery/study",
        "software-delivery-governed/governed-delivery",
    } <= set(SHIPPED)


@pytest.mark.parametrize("label", sorted(EVERY))
def test_a_shipped_workflow_is_warned_about_neither(label: str) -> None:
    """No shipped workflow merges without a guarantee on the diff, and none asks a run's scope of a
    transition the platform takes."""
    assert not {MERGE_WARNING, RUN_SCOPE_WARNING} & set(EVERY[label][1])


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_a_shipped_workflow_that_merges_is_warned_about_nothing(label: str) -> None:
    assert EVERY[label][1] == []


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_a_shipped_merge_transition_carries_no_secrets(label: str) -> None:
    _, merge = SHIPPED[label]
    assert "no_secrets" in merge.gate_names(), f"{label}: `{merge.id}` merges a diff nobody read for secrets"


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_a_shipped_merge_transition_holds_what_the_earlier_steps_ask_of_the_diff(label: str) -> None:
    workflow, merge = SHIPPED[label]
    at_the_merge = {gate.name: gate.params for gate in merge.gates}
    asked = [
        (transition.id, gate)
        for transition in workflow.transitions
        if transition is not merge
        for gate in transition.gates
        if gate.name in DIFF_GATES and not judged_on_a_run(gate.name, gate.params)
    ]
    assert asked or "no_secrets" in at_the_merge
    for step, gate in asked:
        assert gate.name in at_the_merge, (
            f"{label}: `{step}` asks `{gate.name}` of the diff and `{merge.id}` does not: "
            "it is not held on the commit that is merged"
        )
        assert _covers(at_the_merge[gate.name], gate.params), (
            f"{label}: `{merge.id}` declares `{gate.name}` with {at_the_merge[gate.name]}, "
            f"which asks less than `{step}` does with {gate.params}"
        )


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_a_shipped_merge_transition_holds_the_scope_over_the_work_item(label: str) -> None:
    """The scope the steps are held to on their own writes is read at the merge on the whole diff.
    Where every such step declares the same `paths`, the merge declares them; otherwise it declares
    none, and holds the paths the policy forbids."""
    workflow, merge = SHIPPED[label]
    [scope] = [gate for gate in merge.gates if gate.name == "scope_respected"]
    assert scope.params.get("over") == OVER_WORK_ITEM, f"{label}: `{merge.id}` reads a run's scope"
    of_the_steps = [
        gate.params.get("paths")
        for transition in workflow.transitions
        for gate in transition.gates
        if gate.name == "scope_respected" and judged_on_a_run(gate.name, gate.params)
    ]
    assert of_the_steps, f"{label}: no step is held to a scope"
    shared = of_the_steps[0] if all(paths == of_the_steps[0] for paths in of_the_steps) else None
    assert scope.params.get("paths") == shared, (label, of_the_steps)


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_no_guarantee_of_a_shipped_merge_transition_is_judged_on_a_run(label: str) -> None:
    """A run's scope there would be about the last step's writes: a commit that landed before that
    step began is not among them."""
    _, merge = SHIPPED[label]
    assert not [gate.name for gate in merge.gates if judged_on_a_run(gate.name, gate.params)], label


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_a_refused_merge_has_somewhere_to_go(label: str) -> None:
    """A refusal follows `on_fail`, a bounded number of times, to a state the work item can leave:
    the path a red CI already takes."""
    workflow, merge = SHIPPED[label]
    retry = merge.on_fail
    assert retry is not None and retry.max_attempts >= 1, f"{label}: `{merge.id}` has no `on_fail`"
    assert merge.timeout_hours, f"{label}: `{merge.id}` waits for its checks without a limit"
    for state in (retry.to, retry.escalate_to):
        assert state in workflow.states and not workflow.states[state].terminal, (label, state)
        assert workflow.transitions_from(state), f"{label}: nothing leaves `{state}`"


def test_advanced_judges_the_release_notes_on_their_step_and_the_whole_change_as_it_opens() -> None:
    """`advanced` asked a scope of the transition that opens the pull request: the platform takes it,
    so it reads the whole diff there, and what the release notes step wrote is judged on that step."""
    workflow, _ = SHIPPED["core/advanced"]
    by_id = {transition.id: transition for transition in workflow.transitions}
    [notes] = [gate for gate in by_id["t-release-notes"].gates if gate.name == "scope_respected"]
    [opening] = [gate for gate in by_id["t-open-pr"].gates if gate.name == "scope_respected"]
    assert judged_on_a_run(notes.name, notes.params)
    assert opening.params == {"over": OVER_WORK_ITEM}


# ───────────────────────── the two readings of `scope_respected` ─────────────────────────

DENY = [".github/workflows/**"]


def _scope(files: list[str], params: dict[str, Any], **context: Any) -> Any:
    return evaluate("scope_respected", GateContext(changed_files=files, denied_paths=DENY, **context), params)


def test_without_over_the_scope_is_the_runs_paths() -> None:
    """The reading it had: the files it is handed against `paths`, else the allowed paths it is
    handed — a run's."""
    inside = _scope(["src/a.py"], {}, allowed_paths=["src/**"])
    assert (inside.passed, inside.detail) == (True, "within the allowed paths")
    outside = _scope(["src/a.py", "docs/b.md"], {}, allowed_paths=["src/**"])
    assert (outside.passed, outside.annotations) == (False, ["docs/b.md"])
    assert _scope(["docs/b.md"], {"paths": ["docs/**"]}, allowed_paths=["src/**"]).passed is True
    assert _scope(["anything"], {}).detail == "no allowed paths declared"
    assert _scope([".github/workflows/ci.yml"], {}, allowed_paths=["**"]).passed is False


def test_over_the_work_item_the_allowed_paths_are_the_declared_ones_only() -> None:
    """The allowed paths handed with the files are a run's, or the work item's: neither is read."""
    whole = {"over": OVER_WORK_ITEM}
    unread = _scope(["src/a.py", "docs/b.md"], whole, allowed_paths=["src/**"])
    assert unread.passed is True
    assert unread.detail == (
        "no allowed paths declared on this transition: only the paths the policy forbids are held"
    )
    declared = _scope(["src/a.py", "docs/b.md"], {**whole, "paths": ["src/**"]}, allowed_paths=["**"])
    assert (declared.passed, declared.annotations) == (False, ["docs/b.md"])
    assert declared.detail == "1 file(s) outside the declared paths"
    assert _scope(["src/a.py"], {**whole, "paths": ["src/**"]}).detail == "within the declared paths"


def test_over_the_work_item_a_forbidden_path_is_refused_whatever_is_declared() -> None:
    for params in ({"over": OVER_WORK_ITEM}, {"over": OVER_WORK_ITEM, "paths": ["**"]}):
        refused = _scope(["src/a.py", ".github/workflows/ci.yml"], params)
        assert (refused.passed, refused.annotations) == (False, [".github/workflows/ci.yml"])
        assert "scope.deny_paths" in refused.detail


def test_a_diff_that_cannot_be_read_refuses_either_reading() -> None:
    for params in ({}, {"over": OVER_WORK_ITEM}):
        unread = _scope([], params, diff_available=False)
        assert unread.passed is False and "diff unavailable" in unread.detail


def test_an_over_the_guarantee_does_not_know_refuses() -> None:
    """The validator refuses it when the workflow is written; a stored one that carries it does not
    pass for having said something else."""
    unknown = _scope(["src/a.py"], {"over": "run"}, allowed_paths=["**"])
    assert unknown.passed is False and "`over: run`" in unknown.detail


def test_a_declaration_says_whether_it_is_judged_on_a_run() -> None:
    assert judged_on_a_run("scope_respected") and judged_on_a_run("scope_respected", {"paths": ["src/**"]})
    assert not judged_on_a_run("scope_respected", {"over": OVER_WORK_ITEM})
    assert not judged_on_a_run("no_secrets") and not judged_on_a_run("diff_size_max", {"over": "x"})


def test_the_process_view_says_which_reading_a_step_carries() -> None:
    on_a_run = (gate_summary("scope_respected", {}), gate_detail("scope_respected", {}))
    assert on_a_run[0] == "the agent stayed within its allowed paths"
    whole = {"over": OVER_WORK_ITEM}
    assert (
        gate_summary("scope_respected", whole) == "the whole change stays out of the paths the policy forbids"
    )
    assert "whole diff" in str(gate_detail("scope_respected", whole))
    assert "only the forbidden ones" in str(gate_detail("scope_respected", whole))
    record = {**whole, "paths": ["docs/adr/**"]}
    assert gate_summary("scope_respected", record).endswith("and within the declared ones")
    assert "only files under `docs/adr/**`" in str(gate_detail("scope_respected", record))
    workflow, merge = SHIPPED["core/default-simple"]
    [step] = [s for s in to_process(workflow) if s["id"] == merge.id]
    [scope] = [g for g in step["gates"] if g["name"] == "scope_respected"]
    assert scope["summary"] == gate_summary("scope_respected", whole) and scope["summary"] in step["sentence"]


# ───────────────────────── the validator ─────────────────────────

WORKFLOW = """apiVersion: choregos/v1
kind: Workflow
metadata: {{ name: delivery, version: 1 }}
actors:
  developer: {{ type: agent, role: implement, model: "profile:standard" }}
  checker: {{ type: agent, role: verify, model: "profile:standard" }}
  platform: {{ type: system }}
  owner: {{ type: human, group: maintainers }}
states:
  inbox: {{ display: Inbox, kind: wait }}
  written: {{ display: Written }}
  checked: {{ display: Checked }}
  in_review: {{ display: In review, kind: wait }}
  integrated: {{ display: Integrated, terminal: true }}
  needs_human: {{ display: Needs a human, kind: wait }}
  abandoned: {{ display: Abandoned, terminal: true }}
transitions:
  - {{ id: t-write, from: inbox, to: written, by: developer, gates: {writing} }}
  - {{ id: t-check, from: written, to: checked, by: checker, gates: [evidence_present] }}
  - {{ id: t-open, from: checked, to: in_review, by: platform, does: open_pr, gates: {opening} }}
  - id: t-merge
    from: in_review
    to: integrated
    by: {by}
    {does}
    gates: {gates}
    timeout_hours: 24
    on_fail: {{ to: needs_human, max_attempts: 1, escalate_to: needs_human }}
  - {{ id: t-abandon, from: needs_human, to: abandoned, by: owner }}
"""
WHOLE = "{ name: scope_respected, params: { over: work_item } }"


def _report(
    gates: str,
    *,
    by: str = "platform",
    does: str = "does: merge_pr",
    writing: str = "[scope_respected, no_secrets]",
    opening: str = "[]",
) -> Any:
    text = WORKFLOW.format(gates=gates, by=by, does=does, writing=writing, opening=opening)
    return parse_workflow(text, strict=False)[1]


def _warnings(code: str, gates: str, **more: str) -> list[Any]:
    report = _report(gates, **more)
    assert report.valid, [e.format() for e in report.errors]
    return [w for w in report.warnings if w.code == code]


def test_a_merge_transition_without_a_diff_guarantee_is_warned_about() -> None:
    [warning] = _warnings(MERGE_WARNING, "[ci_green, review_approved]")
    assert warning.path == "transitions[3]" and warning.line is not None
    assert "`integrated`" in warning.message and "`no_secrets`" in warning.message


@pytest.mark.parametrize(
    "gates",
    [
        "[ci_green, no_secrets]",
        "[{ name: diff_size_max, params: { files: 20 } }]",
        "[docs_updated]",
        f"[ci_green, {WHOLE}]",
    ],
)
def test_a_merge_transition_that_reads_the_diff_is_not_warned_about(gates: str) -> None:
    """The scope over the work item's whole diff is a guarantee held on what is merged."""
    assert _warnings(MERGE_WARNING, gates) == []


def test_a_guarantee_judged_on_a_run_does_not_hold_the_merge() -> None:
    """`scope_respected` without `over` reads the last step's writes, not what is merged: what counts
    is the declaration, not the name."""
    assert len(_warnings(MERGE_WARNING, "[ci_green, scope_respected]")) == 1
    paths = '[ci_green, { name: scope_respected, params: { paths: ["src/**"] } }]'
    assert len(_warnings(MERGE_WARNING, paths)) == 1


def test_only_a_transition_that_merges_is_warned_about() -> None:
    assert _warnings(MERGE_WARNING, "[ci_green]", does="") == [], "a system transition that merges nothing"
    assert _warnings(MERGE_WARNING, "[]", by="owner", does="") == [], "a person's step"


def test_a_runs_scope_on_a_transition_the_platform_takes_is_warned_about() -> None:
    [at_the_merge] = _warnings(RUN_SCOPE_WARNING, "[ci_green, no_secrets, scope_respected]")
    assert at_the_merge.path == "transitions[3].gates[2]" and at_the_merge.line is not None
    assert "`integrated`" in at_the_merge.message
    assert "{ name: scope_respected, params: { over: work_item } }" in at_the_merge.message
    [as_it_opens] = _warnings(RUN_SCOPE_WARNING, f"[no_secrets, {WHOLE}]", opening="[scope_respected]")
    assert as_it_opens.path == "transitions[2].gates[0]", (
        "any transition the platform takes, not the merge only"
    )


def test_a_scope_over_the_work_item_or_on_a_step_that_writes_is_not_warned_about() -> None:
    assert _warnings(RUN_SCOPE_WARNING, f"[no_secrets, {WHOLE}]", opening=f"[{WHOLE}]") == []
    assert _warnings(RUN_SCOPE_WARNING, "[no_secrets]") == [], "the developer's step keeps its run's scope"
    assert _warnings(RUN_SCOPE_WARNING, "[scope_respected]", by="owner", does="") == [], "a person's step"


@pytest.mark.parametrize("over", ["run", "branch", "true"])
def test_an_over_that_is_not_work_item_is_refused(over: str) -> None:
    report = _report(f"[no_secrets, {{ name: scope_respected, params: {{ over: {over} }} }}]")
    [error] = [e for e in report.errors if e.code == "gate.scope_over_unknown"]
    assert not report.valid
    assert error.path == "transitions[3].gates[1].params.over" and error.line is not None
    assert "`work_item`" in error.message
    assert not [w for w in report.warnings if w.code == RUN_SCOPE_WARNING], "one finding per declaration"


def test_an_unknown_over_is_refused_on_a_step_that_writes_too() -> None:
    report = _report("[no_secrets]", writing="[{ name: scope_respected, params: { over: step } }]")
    assert [e.code for e in report.errors] == ["gate.scope_over_unknown"]
