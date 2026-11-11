"""The shipped workflows hold their diff guarantees to the merge (S65-07, ADR 0152).

A merge names the commit its own transition's gates judged: a guarantee on the diff that only an
earlier step carries is not held on what is merged. Every workflow the repository ships that merges
therefore declares, on its merge transition, `no_secrets` and each guarantee an earlier step asks of
the work item's whole diff, with the same parameters. `scope_respected` is the exception, and is not
declared there: it is judged on what a run wrote (`RUN_SCOPED_GATES`), and on a merge transition the
run is the last step's.

The validator warns about a merge transition that carries none (`workflow.no_diff_gate_at_merge`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import SystemActor
from choregos_contracts.workflow import Transition, Workflow, transition_effect
from choregos_core import parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, template_yaml
from choregos_core.gates import DIFF_GATES, RUN_SCOPED_GATES

ROOT = Path(__file__).resolve().parents[3]
#: The guarantees read on the work item's whole diff, at the commit the evaluation judges.
HELD = DIFF_GATES - RUN_SCOPED_GATES


def _merge_of(workflow: Workflow) -> Transition | None:
    for transition in workflow.transitions:
        if (
            isinstance(workflow.actor_of(transition), SystemActor)
            and transition_effect(transition) == "merge_pr"
        ):
            return transition
    return None


def _shipped() -> dict[str, tuple[Workflow, Transition, list[str]]]:
    """Every workflow the repository ships that merges — the core's and those of `templates/` — with its
    merge transition and the validator's warnings."""
    texts = {f"core/{name}": template_yaml(name) for name in TEMPLATE_NAMES}
    for path in sorted((ROOT / "templates").glob("*/workflows/*.yaml")):
        texts[f"{path.parents[1].name}/{path.stem}"] = path.read_text(encoding="utf-8")
    found = {}
    for label, text in texts.items():
        workflow, report = parse_workflow(text, strict=True)
        merge = _merge_of(workflow)
        if merge is not None:
            found[label] = (workflow, merge, [warning.format() for warning in report.warnings])
    return found


SHIPPED = _shipped()


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


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_a_shipped_workflow_that_merges_is_warned_about_nothing(label: str) -> None:
    assert SHIPPED[label][2] == []


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_a_shipped_merge_transition_carries_no_secrets(label: str) -> None:
    _, merge, _ = SHIPPED[label]
    assert "no_secrets" in merge.gate_names(), f"{label}: `{merge.id}` merges a diff nobody read for secrets"


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_a_shipped_merge_transition_holds_what_the_earlier_steps_ask_of_the_diff(label: str) -> None:
    workflow, merge, _ = SHIPPED[label]
    at_the_merge = {gate.name: gate.params for gate in merge.gates}
    asked = [
        (transition.id, gate)
        for transition in workflow.transitions
        if transition is not merge
        for gate in transition.gates
        if gate.name in HELD
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
def test_a_shipped_merge_transition_declares_no_guarantee_judged_on_a_run(label: str) -> None:
    """Declared there, `scope_respected` would be judged on the last step's writes: a commit that
    landed before that step began is not among them."""
    _, merge, _ = SHIPPED[label]
    assert not RUN_SCOPED_GATES & set(merge.gate_names()), label


@pytest.mark.parametrize("label", sorted(SHIPPED))
def test_a_refused_merge_has_somewhere_to_go(label: str) -> None:
    """A refusal follows `on_fail`, a bounded number of times, to a state the work item can leave:
    the path a red CI already takes."""
    workflow, merge, _ = SHIPPED[label]
    retry = merge.on_fail
    assert retry is not None and retry.max_attempts >= 1, f"{label}: `{merge.id}` has no `on_fail`"
    assert merge.timeout_hours, f"{label}: `{merge.id}` waits for its checks without a limit"
    for state in (retry.to, retry.escalate_to):
        assert state in workflow.states and not workflow.states[state].terminal, (label, state)
        assert workflow.transitions_from(state), f"{label}: nothing leaves `{state}`"


# ───────────────────────── the validator's warning ─────────────────────────

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
  - {{ id: t-write, from: inbox, to: written, by: developer, gates: [scope_respected, no_secrets] }}
  - {{ id: t-check, from: written, to: checked, by: checker, gates: [evidence_present] }}
  - {{ id: t-open, from: checked, to: in_review, by: platform, does: open_pr }}
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


def _warnings(gates: str, *, by: str = "platform", does: str = "does: merge_pr") -> list[Any]:
    _, report = parse_workflow(WORKFLOW.format(gates=gates, by=by, does=does), strict=False)
    assert report.valid, [e.format() for e in report.errors]
    return [w for w in report.warnings if w.code == "workflow.no_diff_gate_at_merge"]


def test_a_merge_transition_without_a_diff_guarantee_is_warned_about() -> None:
    [warning] = _warnings("[ci_green, review_approved]")
    assert warning.path == "transitions[3]" and warning.line is not None
    assert "`integrated`" in warning.message and "`no_secrets`" in warning.message


@pytest.mark.parametrize(
    "gates",
    ["[ci_green, no_secrets]", "[{ name: diff_size_max, params: { files: 20 } }]", "[docs_updated]"],
)
def test_a_merge_transition_that_reads_the_diff_is_not_warned_about(gates: str) -> None:
    assert _warnings(gates) == []


def test_a_guarantee_judged_on_a_run_does_not_hold_the_merge() -> None:
    """`scope_respected` on the merge transition reads the last step's writes, not what is merged."""
    assert len(_warnings("[ci_green, scope_respected]")) == 1


def test_only_a_transition_that_merges_is_warned_about() -> None:
    assert _warnings("[ci_green]", does="") == [], "a system transition that merges nothing"
    assert _warnings("[]", by="owner", does="") == [], "a person's step"
