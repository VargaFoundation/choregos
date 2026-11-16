# SPDX-License-Identifier: Apache-2.0
"""An action may end in doubt, and a write at an outpost says how it is delivered (S65-14, ADR 0149 §5-6).

Contract only: what the documents say. A write through an outpost is delivered at most once; when the
instance that claimed it never answers, its effect and its action end `in_doubt`, which nothing but a
person's settlement moves on (S65-26).
Whoever opens such a write says one of two things in the operation's rule, with the words the contract
already had for a platform argument, and the refusal of a rule that says neither names both.
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


def _validator(name: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"components": SPEC["components"], **SCHEMAS[name]})


def _described(operation_id: str) -> str:
    for item in SPEC["paths"].values():
        for operation in item.values():
            if isinstance(operation, dict) and operation.get("operationId") == operation_id:
                return " ".join(str(operation.get("description", "")).split())
    raise AssertionError(operation_id)


def test_an_action_s_statuses_are_the_enum_s_and_in_doubt_is_one() -> None:
    from choregos_contracts import ActionStatus

    declared = SCHEMAS["Action"]["properties"]["status"]
    assert set(declared["enum"]) == {status.value for status in ActionStatus}
    assert ActionStatus.IN_DOUBT.value == "in_doubt"
    said = declared["description"]
    # Nothing the platform does by itself replays it or undoes anything: a person settles it (S65-26).
    assert "Nothing replays it" in said and "compensated automatically" in said
    assert "Only a person moves it on" in said and "`settleAction`" in said


def test_an_effect_may_be_in_doubt_and_says_what_was_sent() -> None:
    effect = SCHEMAS["ActionEffect"]["properties"]
    statuses = ["started", "done", "compensated", "compensation_failed", "in_doubt"]
    assert effect["status"]["enum"][:5] == statuses
    shown = effect["result"]["description"]
    for name in ("outpost", "endpoint", "operation", "call_id", "instance_id", "claimed_at"):
        assert f"`{name}`" in shown, name
    assert "`arguments_sha256`" in shown and "`again`" in shown


def test_an_action_that_ended_in_doubt_is_said_by_the_existing_terminal_event() -> None:
    """No event type was added for the doubt: `choregos.action.failed` carries the status the action
    ended with. The one type added since says a person settled it (S65-26)."""
    from choregos_contracts.enums import EventType

    actions = sorted(kind.value for kind in EventType if kind.value.startswith("choregos.action."))
    assert actions == [
        "choregos.action.decided",
        "choregos.action.effect_compensated",
        "choregos.action.effect_done",
        "choregos.action.failed",
        "choregos.action.proposed",
        "choregos.action.settled",
        "choregos.action.succeeded",
    ]


def test_the_rule_says_a_platform_argument_with_the_words_a_plugin_server_uses() -> None:
    """One way to say it: the list a plugin server's operation declares, said by the organisation's
    rule for a discovered tool, which declares nothing."""
    plugin = json.loads((CONTRACTS / "schemas" / "plugin-server.schema.json").read_text())
    found: list[list[str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "platform_arguments" in node and isinstance(node["platform_arguments"], dict):
                found.append(list(node["platform_arguments"]["items"]["enum"]))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(plugin)
    assert found, "the plugin server's schema no longer declares `platform_arguments`"
    assert SCHEMAS["PlatformArgument"]["enum"] == found[0] == ["action_key", "work_item_key"]
    for shape in ("ConnectorOperation", "OperationPatch"):
        listed = SCHEMAS[shape]["properties"]["platform_arguments"]
        assert listed["items"] == {"$ref": "#/components/schemas/PlatformArgument"}, shape
        assert listed["uniqueItems"] is True, shape


def test_the_two_statements_are_on_the_rule_as_it_is_read_and_as_it_is_written() -> None:
    read, written = SCHEMAS["ConnectorOperation"]["properties"], SCHEMAS["OperationPatch"]["properties"]
    assert read["in_doubt_accepted"]["type"] == "boolean" and read["in_doubt_accepted"]["default"] is False
    assert read["platform_arguments"]["type"] == "array" and read["platform_arguments"]["default"] == []
    # Written: absent leaves it, null clears it.
    assert written["in_doubt_accepted"]["type"] == ["boolean", "null"]
    assert written["platform_arguments"]["type"] == ["array", "null"]
    patch = _validator("OperationPatch")
    for body in (
        {"policy": "approval", "in_doubt_accepted": True},
        {"policy": "allowed", "platform_arguments": ["action_key"]},
        {"platform_arguments": None, "in_doubt_accepted": None},
        {"platform_arguments": []},
    ):
        assert not list(patch.iter_errors(body)), body
    for body in (
        {"in_doubt_accepted": "yes"},
        {"platform_arguments": ["idempotency_key"]},
        {"platform_arguments": ["action_key", "action_key"]},
        {"platform_arguments": "action_key"},
    ):
        assert list(patch.iter_errors(body)), body
    operation = _validator("ConnectorOperation")
    row = {"name": "create_issue", "access": "write", "policy": "approval"}
    assert not list(operation.iter_errors(row | {"platform_arguments": ["action_key"]}))
    assert not list(operation.iter_errors(row | {"in_doubt_accepted": True}))
    assert list(operation.iter_errors(row | {"in_doubt_accepted": None}))


def test_the_refusal_of_a_write_opened_with_neither_statement_names_both() -> None:
    rule = _described("updateConnectorOperation")
    assert "`mcp-outpost`" in rule and "at most once" in rule
    assert "ADR 0149" in rule and "ADR 0101" in rule
    assert "`platform_arguments`" in rule and "`action_key`" in rule and "`in_doubt_accepted`" in rule
    assert "names both" in rule
    assert "S65-14" not in rule, "the rule is here: the contract no longer waits for it"
