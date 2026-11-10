"""The rules of an argument constraint that need no database (S60-06, ADR 0128)."""

from __future__ import annotations

import pytest
from choregos_core.argument_grants import (
    constraint_error,
    interpolate,
    narrowed,
    properties_named,
    violation,
)

ONLY_SHOP = {"properties": {"name": {"const": "shop"}}, "required": ["name"]}


def test_a_call_inside_the_constraint_passes_and_outside_names_the_path() -> None:
    assert violation(ONLY_SHOP, {"name": "shop", "revision": "main"}) is None
    path, message = violation(ONLY_SHOP, {"name": "billing"}) or ("", "")
    assert path == "/name" and "shop" in message
    assert (violation(ONLY_SHOP, {}) or ("",))[0] == "/", "a missing required argument is at the root"


def test_only_project_is_a_placeholder_and_only_in_values() -> None:
    schema = {"properties": {"{project}": {"const": "{project}-prod"}}, "enum": ["{org}", ["{project}"]]}
    assert interpolate(schema, "iris") == {
        "properties": {"{project}": {"const": "iris-prod"}},
        "enum": ["{org}", ["iris"]],
    }


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.test/s.json"},
        {"properties": {"a": {"$dynamicRef": "#x"}}},
        {"allOf": [{"$id": "https://example.test/x"}]},
        {"type": 12},
        {"properties": {"a": {"const": "x" * 9000}}},
    ],
)
def test_what_the_platform_does_not_read_is_refused(schema: dict[str, object]) -> None:
    assert constraint_error(schema)


def test_a_constraint_names_its_properties_through_the_combinators() -> None:
    schema = {
        "properties": {"a": {}},
        "required": ["b"],
        "anyOf": [{"required": ["c"]}, {"allOf": [{"required": ["d"]}]}],
    }
    assert properties_named(schema) == {"a", "b", "c", "d"}
    assert constraint_error(ONLY_SHOP) is None


def test_the_announced_schema_is_the_operations_and_every_bound() -> None:
    base = {"type": "object", "properties": {"name": {"type": "string"}}}
    assert narrowed(base, []) == base
    assert narrowed(None, []) == {"type": "object"}
    assert narrowed(base, [ONLY_SHOP]) == {"type": "object", "allOf": [base, ONLY_SHOP]}
