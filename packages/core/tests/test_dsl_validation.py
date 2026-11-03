"""DSL validation: the three templates pass, twelve faulty documents are located."""

from __future__ import annotations

from pathlib import Path

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
            "schema errors must be located"
        )
        return [i.code for i in exc.issues]
    assert not report.valid, "this document should have been refused"
    for issue in report.errors:
        assert issue.path, "every error carries a path"
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
        "  done: { display: Done, terminal: true }\n  forgotten: { display: Forgotten }",
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
    """A workflow without a waiting state can never ask for arbitration: a warning."""
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
    assert "line" in issue.format()


def test_13_guarantee_with_nothing_to_check() -> None:
    """`outputs_present` without `outputs:` looks at nothing. The engine also refuses at the moment
    of ruling, but a wrong workflow must fail when it is written: otherwise it is pinned
    on a work item and the guarantee is only discovered in flight, green and empty."""
    unfilled = (
        "  - {id: t1, from: inbox, to: doing, by: dev, gates: [outputs_present], on_fail: "
        + RETRY
        + "}\n  - {id: t2, from: doing, to: done, by: ci}\n"
    )
    assert "gate.nothing_to_check" in _errors(wf_text(unfilled))

    filled = unfilled.replace("gates: [outputs_present]", "outputs: [profiles], gates: [outputs_present]")
    _wf, report = parse_workflow(wf_text(filled), strict=False)
    assert report.valid, [i.format() for i in report.errors]

    assumed = unfilled.replace(
        "gates: [outputs_present]",
        "gates: [{name: outputs_present, params: {allow_empty: true}}]",
    )
    _wf2, report2 = parse_workflow(wf_text(assumed), strict=False)
    assert report2.valid, "the absence of outputs can be assumed, but it must be written"


def test_14_evidence_facts_without_keys() -> None:
    """The same rule for the trade-evidence guarantee: without `keys:`, it reads nothing."""
    unfilled = (
        "  - {id: t1, from: inbox, to: doing, by: dev, gates: [evidence_facts], on_fail: "
        + RETRY
        + "}\n  - {id: t2, from: doing, to: done, by: ci}\n"
    )
    assert "gate.nothing_to_check" in _errors(wf_text(unfilled))

    filled = unfilled.replace(
        "gates: [evidence_facts]",
        "gates: [{name: evidence_facts, params: {keys: [profiles_kept]}}]",
    )
    _wf, report = parse_workflow(wf_text(filled), strict=False)
    assert report.valid, [i.format() for i in report.errors]


def test_15_a_trade_role_is_a_name_in_its_own_right() -> None:
    """Before 2026-09-24 one had to write `role: custom` + `playbook: sourcing`: it
    worked, but the board showed "custom" for all the steps of a trade and
    the evals did not know what it was about (ADR 0012, limit no. 4)."""
    trade = BASE.replace("dev: {{ type: agent, role: implement }}", "dev: {{ type: agent, role: sourcing }}")
    _wf, report = parse_workflow(trade.format(transitions=OK_TRANSITIONS), strict=False)
    assert report.valid, [i.format() for i in report.errors]

    # Without a resolvable playbook, a warning and not an error: it is up to the deployment
    # to bring it, and the validator does not have to know what it will mount.
    codes = [i.code for i in report.warnings]
    assert "role.playbook_not_found" in codes, codes


def test_16_a_deployment_playbook_silences_the_warning(tmp_path: Path) -> None:
    """The deployment's playbook silences the warning: warning when it is
    there would be a warning people learn to ignore."""
    import os

    (tmp_path / "sourcing.md").write_text("You do sourcing.", encoding="utf-8")
    previous = os.environ.get("CHOREGOS_PLAYBOOKS_DIR")
    os.environ["CHOREGOS_PLAYBOOKS_DIR"] = str(tmp_path)
    try:
        trade = BASE.replace(
            "dev: {{ type: agent, role: implement }}", "dev: {{ type: agent, role: sourcing }}"
        )
        _wf, report = parse_workflow(trade.format(transitions=OK_TRANSITIONS), strict=False)
    finally:
        if previous is None:
            del os.environ["CHOREGOS_PLAYBOOKS_DIR"]
        else:
            os.environ["CHOREGOS_PLAYBOOKS_DIR"] = previous
    # The base fixture carries other warnings (no human waiting state):
    # what matters here is that there is NONE about the playbook.
    assert "role.playbook_not_found" not in [i.code for i in report.warnings], [
        i.format() for i in report.warnings
    ]


def test_16b_a_registry_agent_needs_no_playbook() -> None:
    """An actor that names a registry agent takes its instructions (ADR 0033): the playbook of
    its role is only a fallback, and warning about its absence would be noise (S20-07)."""
    trade = BASE.replace(
        "dev: {{ type: agent, role: implement }}",
        "dev: {{ type: agent, role: coordination, agent: onboarding-coordinator }}",
    )
    _wf, report = parse_workflow(trade.format(transitions=OK_TRANSITIONS), strict=False)
    assert report.valid, [i.format() for i in report.errors]
    assert "role.playbook_not_found" not in [i.code for i in report.warnings]


def test_17_a_role_that_is_not_an_identifier_is_refused() -> None:
    """A role serves as the FILE NAME of the playbook: a space or a slash
    would make it look elsewhere than planned."""
    bad = BASE.replace(
        "dev: {{ type: agent, role: implement }}", 'dev: {{ type: agent, role: "../secrets" }}'
    )
    assert any(code.startswith("schema.") for code in _errors(bad.format(transitions=OK_TRANSITIONS)))


def test_tool_called_without_a_tool_is_refused_when_written() -> None:
    """A `tool_called` guarantee without `tools:` would have nothing to check: the DSL refuses it."""
    from choregos_core.dsl.parser import parse_workflow

    source = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: t, version: 1 }
actors:
  a: { type: agent, role: sourcing }
states:
  start: { display: Start }
  end: { display: End, terminal: true }
transitions:
  - id: t1
    from: start
    to: end
    by: a
    gates:
      - name: tool_called
"""
    _, report = parse_workflow(source, strict=False)
    assert not report.valid
    assert any(e.code == "gate.nothing_to_check" and "tool_called" in e.message for e in report.errors)
    ok = source.replace(
        "      - name: tool_called", "      - { name: tool_called, params: { tools: [verify_address] } }"
    )
    _, report = parse_workflow(ok, strict=False)
    assert not [e for e in report.errors if e.code == "gate.nothing_to_check"]


def test_the_escalation_of_a_human_to_an_unknown_actor_is_refused() -> None:
    """Found while splitting `_check_references` (2026-09-25): the actors loop re-read the
    variable of the transitions loop, and this error was never reported."""
    yaml_text = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: t, version: 1 }
actors:
  dev: { type: agent, role: implement, model: "profile:standard" }
  owner: { type: human, group: maintainers, escalate_to: ghost }
states:
  inbox: { display: Inbox }
  done: { display: Done, terminal: true }
transitions:
  - { id: t1, from: inbox, to: done, by: dev }
"""
    _, report = parse_workflow(yaml_text, strict=False)
    assert any(e.code == "actor.unknown" and "ghost" in e.message for e in report.errors), report.errors


def _with_guarantee(name: str, params: str = "") -> str:
    """default-simple, with one more guarantee on t-verify."""
    from choregos_core.dsl import template_yaml

    guarantee = f"{{ name: {name}{', params: ' + params if params else ''} }}"
    return template_yaml("default-simple").replace(
        "    by: checker\n    gates: [evidence_present]",
        f"    by: checker\n    gates: [evidence_present, {guarantee}]",
    )


def test_outputs_in_without_values_is_refused() -> None:
    from choregos_core.dsl import parse_workflow

    _, report = parse_workflow(_with_guarantee("outputs_in"), strict=False)
    assert "gate.nothing_to_check" in {e.code for e in report.errors}
    _, report = parse_workflow(
        _with_guarantee("outputs_in", "{ values: { verdict: [approve] } }"), strict=False
    )
    assert "gate.nothing_to_check" not in {e.code for e in report.errors}


def test_markdown_sections_without_paths_is_refused() -> None:
    from choregos_core.dsl import parse_workflow

    _, report = parse_workflow(_with_guarantee("markdown_sections"), strict=False)
    assert "gate.nothing_to_check" in {e.code for e in report.errors}


def test_without_an_agent_that_writes_code_a_pr_requires_no_verify() -> None:
    """A study merges an ADR: there is nothing to test before its PR (S21-22). An `implement` agent
    without `verify` is still warned."""
    study = BASE.format(
        transitions="  - { id: t-adr, from: inbox, to: doing, by: dev }\n"
        "  - { id: t-pr, from: doing, to: done, by: ci, does: open_pr }"
    )
    _, with_code = parse_workflow(study, strict=False)
    assert "workflow.no_verify_before_pr" in {w.code for w in with_code.warnings}
    _, without_code = parse_workflow(study.replace("role: implement", "role: architect"), strict=False)
    assert "workflow.no_verify_before_pr" not in {w.code for w in without_code.warnings}
