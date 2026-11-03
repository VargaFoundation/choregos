# SPDX-License-Identifier: Apache-2.0
"""ADR 0120: a decision step and a decision record, read the same by the schemas and the models.

The examples only exercise the fields they carry; these tests compare the declarations and make the
schema and the model accept and refuse the same documents.
"""

from __future__ import annotations

import copy
from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_contracts import (
    ActorType,
    DecisionActor,
    DecisionOutcome,
    DecisionRecord,
    Workflow,
)
from jsonschema import Draft202012Validator, RefResolver
from pydantic import ValidationError

from .test_contracts import _compare, _load


def _validator(schema_name: str) -> Draft202012Validator:
    schema = contracts.load_schema(schema_name)
    store = {contracts.load_schema(n)["$id"]: contracts.load_schema(n) for n in contracts.SCHEMA_FILES}
    return Draft202012Validator(
        schema, resolver=RefResolver(base_uri=schema["$id"], referrer=schema, store=store)
    )


DECISION: dict[str, Any] = {
    "type": "decision",
    "role": "route_ticket",
    "state": ["title", "description", "fields.department", "outputs.summary"],
    "questions": {
        "route": {"type": "choice", "instructions": "Which team handles it?", "options": ["billing", "tech"]},
        "urgent": {"type": "yes_no", "instructions": "Must it be handled today?"},
        "risk": {"type": "score", "instructions": "How risky is it?", "levels": ["low", "medium", "high"]},
    },
}


def _workflow_with(actor: dict[str, Any]) -> dict[str, Any]:
    """The default workflow, with a `router` actor that its first transition runs."""
    workflow = copy.deepcopy(_load("workflow.default-simple.json"))
    workflow["actors"]["router"] = actor
    workflow["transitions"][0]["by"] = "router"
    return workflow


def _both(document: dict[str, Any]) -> tuple[bool, bool]:
    """(the schema accepts it, the model accepts it)."""
    schema_ok = not list(_validator("workflow.schema.json").iter_errors(document))
    try:
        Workflow.model_validate(document)
        model_ok = True
    except ValidationError:
        model_ok = False
    return schema_ok, model_ok


def test_the_decision_actor_declares_the_same_fields_in_the_schema_and_the_model() -> None:
    gaps: list[str] = []
    _compare(
        "workflow.decisionActor",
        contracts.load_schema("workflow.schema.json")["$defs"]["decisionActor"],
        DecisionActor,
        gaps,
    )
    assert not gaps, "\n".join(gaps)


def test_the_decision_record_declares_the_same_fields_in_the_schema_and_the_model() -> None:
    schema = contracts.load_schema("decision-record.schema.json")
    gaps: list[str] = []
    _compare("decision-record", schema, DecisionRecord, gaps)
    _compare("decision-record.outcome", schema["$defs"]["outcome"], DecisionOutcome, gaps)
    assert not gaps, "\n".join(gaps)


def test_a_decision_step_is_accepted_by_both_and_writes_two_outputs_per_question() -> None:
    assert _both(_workflow_with(DECISION)) == (True, True)
    actor = Workflow.model_validate(_workflow_with(DECISION)).actors["router"]
    assert isinstance(actor, DecisionActor)
    assert actor.type == ActorType.DECISION
    assert actor.model == "platform/decide"
    assert actor.timeout_seconds == 15
    assert actor.outputs == [
        "route",
        "route_confidence",
        "urgent",
        "urgent_confidence",
        "risk",
        "risk_confidence",
    ]


def _without(key: str) -> dict[str, Any]:
    return {k: v for k, v in DECISION.items() if k != key}


def _question(name: str, question: dict[str, Any]) -> dict[str, Any]:
    return DECISION | {"questions": {name: question}}


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(_without("questions"), id="no questions"),
        pytest.param(_without("state"), id="no state"),
        pytest.param(_without("role"), id="no role"),
        pytest.param(DECISION | {"questions": {}}, id="empty questions"),
        pytest.param(DECISION | {"state": []}, id="empty state"),
        pytest.param(DECISION | {"state": ["memory"]}, id="state outside the work item"),
        pytest.param(DECISION | {"state": ["fields.Department"]}, id="field name not an identifier"),
        pytest.param(DECISION | {"state": ["title", "title"]}, id="state named twice"),
        pytest.param(DECISION | {"timeout_seconds": 0}, id="no time"),
        pytest.param(DECISION | {"timeout_seconds": 121}, id="too long"),
        pytest.param(DECISION | {"command": ["true"]}, id="a job field"),
        pytest.param(
            _question("Route", {"type": "yes_no", "instructions": "x"}), id="output name not an identifier"
        ),
        pytest.param(
            _question("route", {"type": "choice", "instructions": "x"}), id="choice without options"
        ),
        pytest.param(
            _question("route", {"type": "choice", "instructions": "x", "options": ["a"]}), id="one option"
        ),
        pytest.param(
            _question("route", {"type": "choice", "instructions": "x", "options": ["a", "a"]}),
            id="same option twice",
        ),
        pytest.param(_question("risk", {"type": "score", "instructions": "x"}), id="score without levels"),
        pytest.param(
            _question("risk", {"type": "score", "instructions": "x", "levels": ["a", "b"] * 6}),
            id="twelve levels",
        ),
        pytest.param(_question("ok", {"type": "yes_no", "instructions": ""}), id="empty instructions"),
        pytest.param(
            _question("ok", {"type": "yes_no", "instructions": "x", "options": ["a", "b"]}),
            id="options on yes_no",
        ),
        pytest.param(_question("ok", {"type": "maybe", "instructions": "x"}), id="unknown type"),
    ],
)
def test_the_schema_and_the_model_refuse_the_same_decision_steps(bad: dict[str, Any]) -> None:
    assert _both(_workflow_with(bad)) == (False, False)


def test_a_decision_record_is_read_the_same_by_both() -> None:
    validator = _validator("decision-record.schema.json")
    record = _load("decision-record.example.json")
    assert not list(validator.iter_errors(record))
    assert DecisionRecord.model_validate(record).outcome == DecisionOutcome.model_validate(record["outcome"])
    pending = record | {"outcome": None, "transition": None}
    assert not list(validator.iter_errors(pending))
    assert DecisionRecord.model_validate(pending).outcome is None
    for bad in (
        record | {"probability": 1.2},
        record | {"probability": -0.1},
        record | {"source": "oracle"},
        record | {"question": "Route"},
        record | {"outcome": {"terminal": "won"}},
        record | {"outcome": {"reopened": False, "why": "x"}},
        record | {"extra": 1},
        {k: v for k, v in record.items() if k != "probability"},
    ):
        assert list(validator.iter_errors(bad)), bad
        with pytest.raises(ValidationError):
            DecisionRecord.model_validate(bad)
