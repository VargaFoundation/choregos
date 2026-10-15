"""Strict validation of what the LLM proposes, and the shipped JSON Schema."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from choregos_quant.errors import InputError
from choregos_quant.strategies import SCHEMA_PATH, build_schema, strategies_schema, validate_strategies


def one(**over: Any) -> dict[str, Any]:
    s: dict[str, Any] = {"id": "s1", "hypothesis": "h", "family": "ema_trend", "params": {"span": 50}}
    s.update(over)
    return {"strategies": [s]}


def test_valid() -> None:
    out = validate_strategies(one())
    assert out[0].family == "ema_trend"


@pytest.mark.parametrize(
    "bad",
    [
        one(family="magic"),
        one(params={"span": 9}),
        one(params={"span": 301}),
        one(params={"span": 50.0}),
        one(params={"span": True}),
        one(params={"span": 50, "extra": 1}),
        one(params={}),
        one(family="sma_cross", params={"fast": 50, "slow": 50}),
        one(family="sma_cross", params={"fast": 60, "slow": 40}),
        one(family="sma_cross", params={"fast": 4, "slow": 40}),
        one(family="momentum", params={"lookback": 19, "skip": 0}),
        one(family="momentum", params={"lookback": 60, "skip": 22}),
        one(family="rsi_reversion", params={"period": 1, "entry": 30, "exit": 60}),
        one(family="rsi_reversion", params={"period": 5, "entry": 41, "exit": 60}),
        one(family="donchian_breakout", params={"entry": 121, "exit": 10}),
        one(id="has space"),
        one(hypothesis=""),
        {"strategies": []},
        {"strategies": [], "extra": 1},
        {"nope": []},
        {"strategies": [{"id": "s1"}]},
    ],
)
def test_rejected(bad: Any) -> None:
    with pytest.raises(InputError):
        validate_strategies(bad)


def test_too_many_and_duplicate_ids() -> None:
    many = {"strategies": [one(id=f"s{i}")["strategies"][0] for i in range(9)]}
    with pytest.raises(InputError, match="too many"):
        validate_strategies(many, max_strategies=8)
    assert len(validate_strategies(many, max_strategies=9)) == 9
    dup = {"strategies": [one()["strategies"][0], copy.deepcopy(one()["strategies"][0])]}
    with pytest.raises(InputError, match="duplicate"):
        validate_strategies(dup)


def test_shipped_schema_is_generated_from_the_bounds() -> None:
    assert json.loads(SCHEMA_PATH.read_text(encoding="utf-8")) == build_schema()
    schema = strategies_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert len(schema["properties"]["strategies"]["items"]["oneOf"]) == 5


def test_schema_agrees_with_python_validation_on_bounds() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(strategies_schema())
    good, bad = one(), one(params={"span": 9})
    assert validator.is_valid(good) and not validator.is_valid(bad)
    assert not validator.is_valid(one(family="magic"))
