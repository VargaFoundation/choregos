"""A terminal state declares how it ends the work item: a success, a request declined, one cancelled."""

from __future__ import annotations

import pytest
from choregos_contracts import State, StateOutcome, load_schema
from choregos_core import ValidationError, parse_workflow
from choregos_core.dsl import to_graph

ACCESS_YAML = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: access, version: 1 }
actors:
  analyst: { type: agent, role: analyse }
  fondation: { type: human, group: fondation }
states:
  inbox: { display: To take, kind: wait }
  to_apply: { display: To apply, kind: wait }
  applied: { display: Applied, terminal: true }
  declined: { display: Declined, terminal: true, outcome: declined }
  handed_back: { display: Handed back, terminal: true, outcome: cancelled }
transitions:
  - { id: t-analyse, from: inbox, to: to_apply, by: analyst }
  - { id: t-apply, from: to_apply, to: applied, by: fondation, on_reject: declined }
  - { id: t-drop, from: to_apply, to: handed_back, by: fondation }
"""


def test_a_terminal_state_is_a_success_unless_it_says_otherwise() -> None:
    workflow, _ = parse_workflow(ACCESS_YAML)
    assert workflow.states["applied"].outcome is StateOutcome.SUCCESS
    assert workflow.states["declined"].outcome is StateOutcome.DECLINED
    assert workflow.states["handed_back"].outcome is StateOutcome.CANCELLED


def test_the_graph_gives_the_board_each_terminal_state_s_outcome() -> None:
    workflow, _ = parse_workflow(ACCESS_YAML)
    nodes = {node["id"]: node for node in to_graph(workflow)["nodes"]}
    assert nodes["applied"]["outcome"] == "success"
    assert nodes["declined"]["outcome"] == "declined"
    assert nodes["handed_back"]["outcome"] == "cancelled"
    # A state that can still move has no outcome: it is not finished.
    assert nodes["to_apply"]["outcome"] is None


def test_only_a_terminal_state_may_declare_an_outcome() -> None:
    with pytest.raises(ValueError, match="only for a terminal state"):
        State(display="Waiting", kind="wait", outcome="declined")
    with pytest.raises(ValidationError):
        parse_workflow(
            ACCESS_YAML.replace(
                "to_apply: { display: To apply, kind: wait }",
                "to_apply: { display: To apply, kind: wait, outcome: declined }",
            )
        )


def test_the_json_schema_knows_the_outcomes() -> None:
    state = load_schema("workflow.schema.json")["$defs"]["state"]["properties"]["outcome"]
    assert state["enum"] == [o.value for o in StateOutcome]
    assert state["default"] == "success"
