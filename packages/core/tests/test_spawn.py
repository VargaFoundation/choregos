"""A fan-out in the DSL (S38-12, ADR 0057): what the validator refuses, and what the contract refuses."""

from __future__ import annotations

import pytest
from choregos_core import ValidationError, parse_workflow

DOC = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: research
  version: 1
  inputs: {{ type: object, properties: {{ symbols: {{ type: array }} }} }}
actors:
  platform: {{ type: system }}
  owner: {{ type: human, group: owner }}
states:
  screened: {{ display: Screened, kind: wait }}
  decided: {{ display: Decided, terminal: true }}
  nothing: {{ display: Nothing, terminal: true }}
transitions:
  - id: t-each
    from: screened
    to: decided
    by: {by}
    spawn: {spawn}
{extra}
"""

SPAWN = "{workflow: review, for_each: fields.symbols, title: 'Review {{{{ item }}}}', succeeded: [adopted]}"
ON_FAIL = "    on_fail: {to: screened, max_attempts: 1, escalate_to: nothing}"


def _codes(by: str = "platform", spawn: str = SPAWN, extra: str = ON_FAIL) -> list[str]:
    try:
        _workflow, report = parse_workflow(DOC.format(by=by, spawn=spawn, extra=extra), strict=False)
    except ValidationError as refused:
        return [i.code for i in refused.issues]
    return [i.code for i in report.errors]


def test_a_platform_fan_out_with_its_on_fail_is_valid() -> None:
    assert _codes() == []


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"by": "owner"}, "spawn.not_system"),
        ({"extra": ON_FAIL + "\n    action: {kind: k, title: t, effects: [{effect: e}]}"}, "spawn.not_alone"),
        ({"spawn": SPAWN.replace("fields.symbols", "fields.tickers")}, "spawn.field_unknown"),
        ({"spawn": SPAWN.replace("succeeded: [adopted]", "min_succeeded: 1")}, "spawn.succeeded_missing"),
        ({"extra": ""}, "spawn.on_fail_missing"),
    ],
)
def test_the_validator_refuses(kwargs: dict[str, str], code: str) -> None:
    assert code in _codes(**kwargs)


@pytest.mark.parametrize(
    "spawn",
    [
        SPAWN.replace("fields.symbols", "item.symbols"),  # only `fields.` or `outputs.`
        SPAWN.replace("fields.symbols", "fields"),  # a path, not a root
        SPAWN.replace("succeeded:", "max_children: 101, succeeded:"),  # bounded
    ],
)
def test_the_contract_refuses(spawn: str) -> None:
    assert any(code.startswith("schema.") for code in _codes(spawn=spawn))
