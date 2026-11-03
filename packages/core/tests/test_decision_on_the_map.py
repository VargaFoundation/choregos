# SPDX-License-Identifier: Apache-2.0
"""A decision step on the map and in the process view (ADR 0121, S52-06).

The console draws who moves a work item from the graph's lanes and says it from the process view.
A decision step fell into the platform's lane and was described as "the platform": a reader could
not tell that a model answers there, which questions it asks, nor which outputs a `when` compares.
The edits the console sends to create and change one are grafted and undone like any other
(ADR 0112).
"""

from __future__ import annotations

from typing import Any

import yaml
from choregos_contracts import Workflow
from choregos_core import parse_workflow
from choregos_core.dsl.edition import editer
from choregos_core.dsl.graph import to_graph, to_mermaid
from choregos_core.dsl.process import to_process

WORKFLOW = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: support, version: 1 }
actors:
  router:
    type: decision
    role: route_ticket
    state: [title, description]
    questions:
      route: { type: choice, instructions: 'Which team handles this request?', options: [billing, tech] }
      urgent: { type: yes_no, instructions: 'Must it be handled today?' }
      severity: { type: score, instructions: 'How bad is it?', levels: [low, medium, high] }
  owner: { type: human, group: support }
states:
  inbox: { display: Inbox }
  review: { display: Review }
  done: { display: Done, terminal: true }
transitions:
  - { id: t-route, from: inbox, to: review, by: router }
  - { id: t-close, from: review, to: done, by: owner }
"""


def _workflow() -> Workflow:
    # The validator refuses a decision step until the engine runs it (S52-04); the map still reads it.
    workflow, _ = parse_workflow(WORKFLOW, strict=False)
    return workflow


def test_a_decision_step_has_its_own_lane_and_names_its_role() -> None:
    graph = to_graph(_workflow())
    lanes = {n["id"]: n["lane"] for n in graph["nodes"]}
    assert lanes == {"inbox": "decision", "review": "human", "done": "terminal"}
    (route,) = [e for e in graph["edges"] if e["id"] == "t-route"]
    assert (route["actor"], route["actor_type"], route["role"]) == ("router", "decision", "route_ticket")
    assert "inbox --> review: router (route_ticket)" in to_mermaid(_workflow())


def test_the_process_view_says_the_model_what_it_reads_and_what_it_answers() -> None:
    (route, close) = to_process(_workflow())
    assert route["actor_type"] == "decision"
    assert route["who"] == (
        "a decision by `platform/decide` on title, description: route (choice of billing, tech), "
        "urgent (yes/no), severity (score low, medium, high)"
    )
    assert route["sentence"].startswith("From “Inbox”, a decision by `platform/decide`")
    assert (route["role"], route["model"]) == ("route_ticket", "platform/decide")
    # What a `when` compares: each answer and its probability.
    assert route["outputs"] == [
        "route",
        "route_confidence",
        "urgent",
        "urgent_confidence",
        "severity",
        "severity_confidence",
    ]
    assert close["actor_type"] == "human" and close["outputs"] == []


DECISION_SPEC: dict[str, Any] = {
    "type": "decision",
    "role": "route_ticket",
    "model": "platform/decide",
    "state": ["title", "description"],
    "questions": {
        "route": {
            "type": "choice",
            "instructions": "Which team: billing or tech?",
            "options": ["billing", "tech"],
        },
        "urgent": {"type": "yes_no", "instructions": "Must it be handled today?"},
    },
    "timeout_seconds": 15,
}


def test_the_console_creates_a_decision_actor_and_undoes_it_to_the_byte() -> None:
    edition = editer(WORKFLOW, [{"op": "add_actor", "name": "triage", "spec": DECISION_SPEC}])
    assert yaml.safe_load(edition.yaml)["actors"]["triage"] == DECISION_SPEC
    workflow, _ = parse_workflow(edition.yaml, strict=False)
    assert workflow.actors["triage"].type == "decision"
    inverse = [o.model_dump(by_alias=True, exclude_none=True) for o in edition.inverse]
    assert editer(edition.yaml, inverse).yaml == WORKFLOW


def test_the_console_edits_a_decision_written_as_lines_and_undoes_it_to_the_byte() -> None:
    """The questions are replaced as indented lines; the state and the timeout as one line each."""
    questions = {
        "route": {"type": "choice", "instructions": "Which team?", "options": ["billing", "tech", "other"]},
    }
    operations = [
        {"op": "set_actor", "name": "router", "field": "questions", "value": questions, "style": "block"},
        {"op": "set_actor", "name": "router", "field": "state", "value": ["title", "fields.customer"]},
        {"op": "set_actor", "name": "router", "field": "timeout_seconds", "value": 30},
        {"op": "set_actor", "name": "router", "field": "model", "value": "platform/decide-large"},
    ]
    edition = editer(WORKFLOW, operations)
    router = yaml.safe_load(edition.yaml)["actors"]["router"]
    assert router["questions"] == questions
    assert (router["state"], router["timeout_seconds"], router["model"]) == (
        ["title", "fields.customer"],
        30,
        "platform/decide-large",
    )
    assert "        instructions: Which team?" in edition.yaml.splitlines(), edition.yaml
    assert editer(edition.yaml, edition.inverse).yaml == WORKFLOW
