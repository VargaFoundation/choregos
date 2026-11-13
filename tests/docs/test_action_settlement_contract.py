# SPDX-License-Identifier: Apache-2.0
"""A person settles an action in doubt (S65-26, ADR 0149 §5): what the contract says.

Contract only. An action one of whose effects was sent and never answered ends `in_doubt`, and the
platform does nothing more by itself. A person checks the system the write was for and records what
they found: the write was `made` (the action resumes after it) or `not_made` (the action fails and
compensates what came before). The route is guarded as an approval is, the effect's journal keeps the
doubt's evidence with the settlement, and a person who settles twice with the same outcome changes
nothing.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import jsonschema
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "packages" / "contracts"
SPEC: dict[str, Any] = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text())
SCHEMAS = SPEC["components"]["schemas"]
ROUTE = "/projects/{id}/actions/{action_id}/settlement"


def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"components": SPEC["components"], **SCHEMAS[name]})


def _settle() -> dict[str, Any]:
    operation: dict[str, Any] = SPEC["paths"][ROUTE]["post"]
    return operation


def test_the_route_is_a_post_named_settle_action_beside_the_decision() -> None:
    operation = _settle()
    assert operation["operationId"] == "settleAction" and operation["tags"] == ["actions"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body == {"$ref": "#/components/schemas/ActionSettlementRequest"}
    answered = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert answered == {"$ref": "#/components/schemas/Action"}
    assert set(operation["responses"]) == {"200", "401", "403", "404", "409", "422"}
    assert "step_up_required" in operation["responses"]["401"]["description"]
    conflict = operation["responses"]["409"]["description"]
    assert "`action_not_in_doubt`" in conflict and "`action_already_settled`" in conflict


def test_it_is_guarded_as_an_approval_is() -> None:
    said = " ".join(_settle()["description"].split())
    for guard in (
        "human session",
        "API token",
        "approvers",
        "`step_up_minutes`",
        "`step_up_required`",
        "separation of duties",
    ):
        assert guard in said, guard


def test_it_says_what_each_outcome_does_to_the_action_and_its_work_item() -> None:
    said = " ".join(_settle()["description"].split())
    assert "`made`: the effect in doubt is recorded `done`" in said and "`approved` again" in said
    assert "`not_made`: the effect is recorded `not_made`" in said and "ends `failed`" in said
    assert "compensated backwards" in said and "work item that waits" in said
    assert "nothing runs within this request" in said
    assert "Settling again with the same outcome answers the action as it is" in said


def test_a_settlement_request_says_an_outcome_and_a_note() -> None:
    request = _validator("ActionSettlementRequest")
    for body in (
        {"outcome": "made", "note": "the ticket OPS-7 is in the tracker"},
        {"outcome": "made", "note": "found", "result": {"key": "OPS-7"}},
        {"outcome": "made", "note": "found", "result": None},
        {"outcome": "not_made", "note": "nothing in the tracker since noon"},
    ):
        assert not list(request.iter_errors(body)), body
    for body in (
        {"outcome": "made"},
        {"note": "found"},
        {"outcome": "made", "note": ""},
        {"outcome": "maybe", "note": "found"},
        {"outcome": "made", "note": "found", "by": "someone@else.test"},
        {"outcome": "made", "note": "x" * 2001},
    ):
        assert list(request.iter_errors(body)), body


def test_the_outcomes_are_the_enum_s() -> None:
    from choregos_contracts import SettlementOutcome

    assert SCHEMAS["SettlementOutcome"]["enum"] == [outcome.value for outcome in SettlementOutcome]
    assert [outcome.value for outcome in SettlementOutcome] == ["made", "not_made"]


def test_the_settlement_is_on_the_action_and_on_its_effect_with_the_doubt_s_evidence() -> None:
    record = {
        "position": 1,
        "outcome": "made",
        "by": "ops@example.test",
        "at": "2026-11-14T10:00:00Z",
        "note": "OPS-7 is there",
        "result": {"key": "OPS-7"},
        "auth_age_seconds": 120,
        "in_doubt": [{"outpost": "factory-floor", "call_id": "c1", "arguments_sha256": "ab"}],
    }
    assert not list(_validator("ActionSettlement").iter_errors(record))
    del record["in_doubt"]
    assert list(_validator("ActionSettlement").iter_errors(record)), "the evidence is kept, always"
    for shape in ("Action", "ActionEffect"):
        settlement = SCHEMAS[shape]["properties"]["settlement"]
        assert settlement["oneOf"] == [{"$ref": "#/components/schemas/ActionSettlement"}, {"type": "null"}]
    effect = SCHEMAS["ActionEffect"]["properties"]["status"]
    assert effect["enum"][-1] == "not_made" and "`not_made`" in effect["description"]


def test_a_person_s_settlement_is_an_event_of_its_own() -> None:
    from choregos_contracts.enums import EventType

    assert EventType.ACTION_SETTLED.value == "choregos.action.settled"
    event = json.loads((CONTRACTS / "schemas" / "event.schema.json").read_text())
    assert "choregos.action.settled" in event["properties"]["type"]["enum"]
