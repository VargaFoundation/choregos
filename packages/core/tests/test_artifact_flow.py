"""The artifact flow: who produces each document and who reads it (S39-13a)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from choregos_core import artifact_flow, parse_workflow, to_graph

ROOT = Path(__file__).resolve().parents[3]
DEV_COMPLEX = ROOT / "templates/github-software-delivery/workflows/dev-complex.yaml"

HEAD = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: inline
  version: 1
  inputs: { type: object, properties: { start_date: { type: string } } }
initial: todo
actors:
  maker: { type: agent, role: custom, playbook: maker }
states:
  todo: { display: To do, kind: wait }
  made: { display: Made }
  done: { display: Done, terminal: true, kind: terminal }
"""


def _flow(text: str) -> dict[str, dict[str, Any]]:
    workflow, _ = parse_workflow(text)
    return {a["name"]: a for a in artifact_flow(workflow)}


def test_dev_complex_flow() -> None:
    workflow, _ = parse_workflow(DEV_COMPLEX.read_text("utf-8"))
    flow = {a["name"]: a for a in artifact_flow(workflow)}
    assert (flow["test_report"]["produced_by"], flow["test_report"]["read_by"]) == (
        ["t-test"],
        ["t-implement"],
    )
    review = flow["review_markdown"]
    assert review["produced_by"] == ["t-review", "t-address-review"]
    assert review["read_by"] == ["t-address-review"] and review["in_pull_request"] is True
    feedback = flow["human_feedback"]
    assert feedback["origin"] == "feedback" and feedback["produced_by"] == ["t-approve-spec", "t-approve-pr"]
    spec = flow["spec_markdown"]
    assert spec["context"] is True and spec["in_pull_request"] is True and spec["origin"] == "step"
    assert flow["verdict"]["context"] is False and flow["verdict"]["in_pull_request"] is False


def test_the_graph_carries_the_flow_and_it_is_deterministic() -> None:
    workflow, _ = parse_workflow(DEV_COMPLEX.read_text("utf-8"))
    assert to_graph(workflow)["artifacts"] == artifact_flow(workflow) == artifact_flow(workflow)


def test_a_read_nothing_produces_is_missing_unless_it_is_a_field() -> None:
    flow = _flow(
        HEAD
        + """
transitions:
  - { id: t-make, from: todo, to: made, by: maker, inputs: [ghost, start_date] }
  - { id: t-end, from: made, to: done, by: maker }
"""
    )
    assert flow["ghost"]["origin"] == "missing" and flow["ghost"]["produced_by"] == []
    assert flow["start_date"]["origin"] == "field" and flow["start_date"]["read_by"] == ["t-make"]


def test_a_wildcard_transition_counts_and_the_type_comes_from_the_producer() -> None:
    flow = _flow(
        HEAD
        + """
transitions:
  - { id: t-make, from: todo, to: made, by: maker, outputs: [deck], output_types: { deck: pptx } }
  - { id: t-end, from: made, to: done, by: maker }
  - { id: t-any, from: "*agent", to: done, by: maker, outputs: [note] }
"""
    )
    assert flow["note"]["produced_by"] == ["t-any"] and flow["note"]["origin"] == "step"
    assert flow["deck"]["type"] == "pptx" and flow["note"]["type"] is None
