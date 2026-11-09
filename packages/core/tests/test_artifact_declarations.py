"""A workflow's `artifacts:` block says only what the platform can honour (ADR 0129)."""

from __future__ import annotations

import pytest
from choregos_core import parse_workflow

WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: {{ name: t, version: 1 }}
actors:
  bot: {{ type: agent, role: triage }}
  owner: {{ type: human, group: po }}
states:
  inbox: {{ display: Inbox }}
  done: {{ display: Done, terminal: true }}
  stuck: {{ display: Stuck, kind: wait }}
transitions:
  - id: t1
    from: inbox
    to: done
    by: bot
    outputs: {outputs}
    output_types: {types}
    on_fail: {{ to: stuck, max_attempts: 1, escalate_to: stuck }}
  - {{ id: t2, from: stuck, to: done, by: owner }}
artifacts:
{artifacts}
"""


def _codes(artifacts: str, outputs: str = "[coverage, severity, report]", types: str = "{}") -> list[str]:
    text = WORKFLOW.format(artifacts=artifacts, outputs=outputs, types=types)
    _, report = parse_workflow(text, strict=False)
    return sorted({issue.code for issue in report.errors})


GOOD = """
  coverage: { type: metric, unit: percent, min: 0, max: 100, better: higher, show: facts, title: Coverage }
  severity:
    type: level
    show: facts
    levels: [{ value: sev3, tone: warn }, { value: sev1, tone: danger }]
  report: { show: hidden }
  diff: { show: facts }
"""


def test_a_full_declaration_is_valid() -> None:
    assert _codes(GOOD) == []


def test_a_scale_the_platform_ships_needs_no_levels() -> None:
    assert _codes("  severity: { type: level, scale: risk, show: facts }") == []


@pytest.mark.parametrize(
    ("artifacts", "code"),
    [
        ("  nobody: { show: tab }", "artifacts.undeclared"),
        ("  severity: { type: pptx }", "artifacts.format"),
        ("  severity: { type: level, show: facts }", "artifacts.scale"),
        ("  severity: { type: metric, scale: risk }", "artifacts.scale"),
        (
            "  severity: { type: level, scale: risk, levels: [{ value: a }, { value: b }] }",
            "artifacts.scale",
        ),
        ("  severity: { type: level, levels: [{ value: a }, { value: a }] }", "artifacts.scale"),
        ("  severity: { type: level, scale: risk, unit: ms }", "artifacts.metric"),
        ("  coverage: { type: metric, min: 5, max: 1 }", "artifacts.metric"),
        ("  report: { show: facts }", "artifacts.facts"),
        ("  report: { type: keyvalue, show: facts }", "artifacts.facts"),
        ("  diff: { type: level, scale: risk }", "artifacts.reserved"),
    ],
)
def test_what_cannot_be_honoured_is_refused_when_written(artifacts: str, code: str) -> None:
    assert code in _codes(artifacts)


def test_no_step_may_declare_the_diff() -> None:
    assert "outputs.reserved" in _codes("  severity: { show: tab }", outputs="[diff, severity]")


def test_a_value_type_needs_its_declaration() -> None:
    assert "output_types.value" in _codes("  severity: { show: tab }", types="{ severity: level }")


def test_two_places_may_not_contradict_each_other() -> None:
    assert "artifacts.conflict" in _codes("  report: { type: keyvalue }", types="{ report: json }")


def test_the_facts_card_stays_readable() -> None:
    names = [f"n{i}" for i in range(9)]
    artifacts = "\n".join(f"  {name}: {{ type: metric, show: facts }}" for name in names)
    assert "artifacts.facts" in _codes(artifacts, outputs="[" + ", ".join(names) + "]")


def test_a_platform_output_can_be_given_a_place_without_a_type() -> None:
    assert _codes("  size: { show: tab }\n  risk: { title: Risk level }", outputs="[size, risk]") == []


@pytest.mark.parametrize("name", ["size", "risk", "verdict"])
def test_the_scale_of_a_result_field_cannot_be_replaced(name: str) -> None:
    artifacts = f"  {name}: {{ levels: [{{ value: minor }}, {{ value: major }}] }}"
    assert "artifacts.scale" in _codes(artifacts, outputs=f"[{name}]")
    assert _codes(f"  {name}: {{ scale: {name} }}", outputs=f"[{name}]") == []
