"""Validation du DSL : les trois templates passent, douze documents fautifs sont localisés."""

from __future__ import annotations

import pytest
from choregos_core import ValidationError, parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, template_yaml

BASE = """
apiVersion: choregos/v1
kind: Workflow
metadata: {{ name: t, version: 1 }}
actors:
  dev: {{ type: agent, role: implement }}
  ci: {{ type: system }}
  owner: {{ type: human, group: po }}
states:
  inbox: {{ display: Inbox }}
  doing: {{ display: Doing }}
  done: {{ display: Done, terminal: true }}
transitions:
{transitions}
defaults:
  from_any_agent_state: {{ on_question: doing }}
"""

RETRY = "{to: inbox, max_attempts: 2, escalate_to: done}"
OK_TRANSITIONS = (
    "  - {id: t1, from: inbox, to: doing, by: dev, on_fail: " + RETRY + "}\n"
    "  - {id: t2, from: doing, to: done, by: ci}\n"
)


def wf_text(transitions: str = OK_TRANSITIONS, **kwargs: str) -> str:
    return BASE.format(transitions=transitions, **kwargs)


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_templates_are_valid(name: str) -> None:
    workflow, report = parse_workflow(template_yaml(name), strict=False)
    assert report.valid, [i.format() for i in report.errors]
    assert report.warnings == [], [i.format() for i in report.warnings]
    assert workflow.metadata.name == name


def test_base_fixture_is_valid() -> None:
    _, report = parse_workflow(wf_text(), strict=False)
    assert report.valid, [i.format() for i in report.errors]


# ───────────────────────── les douze cas invalides ─────────────────────────


def _errors(text: str) -> list[str]:
    try:
        _workflow, report = parse_workflow(text, strict=False)
    except ValidationError as exc:
        assert all(i.line is not None for i in exc.issues if i.code.startswith("schema.")), (
            "les erreurs de schéma doivent être localisées"
        )
        return [i.code for i in exc.issues]
    assert not report.valid, "ce document aurait dû être refusé"
    for issue in report.errors:
        assert issue.path, "toute erreur porte un chemin"
    return [i.code for i in report.errors]


def test_01_unknown_target_state() -> None:
    codes = _errors(wf_text("  - {id: t1, from: inbox, to: nulle_part, by: dev}\n"))
    assert "state.unknown" in codes


def test_02_unknown_source_state() -> None:
    codes = _errors(
        wf_text(
            "  - {id: t1, from: ailleurs, to: done, by: dev}\n  - {id: t2, from: inbox, to: doing, by: ci}\n"
        )
    )
    assert "state.unknown" in codes


def test_03_unknown_actor() -> None:
    codes = _errors(
        wf_text("  - {id: t1, from: inbox, to: doing, by: fantome}\n  - {from: doing, to: done, by: ci}\n")
    )
    assert "actor.unknown" in codes


def test_04_unknown_gate() -> None:
    codes = _errors(
        wf_text(
            "  - {id: t1, from: inbox, to: doing, by: dev, gates: [licorne_verte]}\n"
            "  - {from: doing, to: done, by: ci}\n"
        )
    )
    assert "gate.unknown" in codes


def test_05_no_terminal_state() -> None:
    text = wf_text().replace("done: { display: Done, terminal: true }", "done: { display: Done }")
    assert "workflow.no_terminal_state" in _errors(text)


def test_06_orphan_state() -> None:
    text = wf_text().replace(
        "  done: { display: Done, terminal: true }",
        "  done: { display: Done, terminal: true }\n  oublie: { display: Oublié }",
    )
    codes = _errors(text)
    assert "state.no_inbound" in codes or "state.orphan" in codes


def test_07_prod_state_without_train() -> None:
    text = wf_text(
        "  - {id: t1, from: inbox, to: doing, by: dev}\n"
        "  - {id: t2, from: doing, to: deployed_prod, by: ci}\n"
    ).replace(
        "  done: { display: Done, terminal: true }",
        "  done: { display: Done, terminal: true }\n  deployed_prod: { display: Prod, terminal: true }",
    )
    assert "prod.requires_train" in _errors(text)


def test_08_unbounded_agent_retry() -> None:
    text = wf_text(
        "  - {id: t1, from: inbox, to: doing, by: ci}\n"
        "  - {id: t2, from: doing, to: inbox, by: dev}\n"
        "  - {id: t3, from: doing, to: done, by: ci}\n"
    )
    assert "transition.unbounded_retry" in _errors(text)


def test_09_retry_targets_unknown_state() -> None:
    text = wf_text(
        "  - {id: t1, from: inbox, to: doing, by: dev, "
        "on_fail: {to: nulle_part, max_attempts: 2, escalate_to: done}}\n"
        "  - {from: doing, to: done, by: ci}\n"
    )
    assert "state.unknown" in _errors(text)


def test_10_transition_without_actor_or_train() -> None:
    codes = _errors(wf_text("  - {id: t1, from: inbox, to: doing}\n  - {from: doing, to: done, by: ci}\n"))
    assert any(code.startswith("schema.") for code in codes)


def test_11_initial_state_terminal() -> None:
    text = wf_text().replace("  inbox: { display: Inbox }", "  inbox: { display: Inbox, terminal: true }")
    assert "workflow.initial_state_terminal" in _errors(text)


def test_12_defaults_point_to_unknown_state() -> None:
    text = wf_text().replace("on_question: doing", "on_question: nulle_part")
    assert "state.unknown" in _errors(text)


def test_13_bad_yaml_is_localised() -> None:
    with pytest.raises(ValidationError) as exc:
        parse_workflow("metadata:\n  name: [oups\n")
    assert exc.value.issues[0].code == "yaml.syntax"
    assert exc.value.issues[0].line is not None


def test_14_unknown_field_is_refused() -> None:
    codes = _errors(
        wf_text().replace("metadata: { name: t, version: 1 }", "metadata: { name: t, version: 1, oups: 1 }")
    )
    assert any(code.startswith("schema.") for code in codes)


def test_warning_when_no_human_state() -> None:
    """Un workflow sans état d'attente ne pourra jamais demander d'arbitrage : avertissement."""
    _, report = parse_workflow(wf_text(), strict=False)
    assert report.valid
    assert any(i.code == "workflow.no_human_state" for i in report.warnings), [
        i.code for i in report.warnings
    ]


def test_no_warning_when_wait_state_exists() -> None:
    text = wf_text().replace("  inbox: { display: Inbox }", "  inbox: { display: Inbox, kind: wait }")
    _, report = parse_workflow(text, strict=False)
    assert not any(i.code == "workflow.no_human_state" for i in report.warnings)


def test_unused_actor_is_reported_as_warning() -> None:
    text = wf_text().replace(
        "  owner: { type: human, group: po }",
        "  owner: { type: human, group: po }\n  ghost: { type: human, group: none }",
    )
    _, report = parse_workflow(text, strict=False)
    codes = [i.code for i in report.warnings]
    assert "actor.unused" in codes


def test_error_message_carries_line_and_column() -> None:
    text = wf_text(
        "  - {id: t1, from: inbox, to: nulle_part, by: dev}\n  - {from: doing, to: done, by: ci}\n"
    )
    _, report = parse_workflow(text, strict=False)
    issue = next(i for i in report.errors if i.code == "state.unknown")
    assert issue.line and issue.line > 0
    assert "transitions[0].to" in (issue.path or "")
    assert "ligne" in issue.format()
