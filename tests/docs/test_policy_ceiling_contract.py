# SPDX-License-Identifier: Apache-2.0
"""The `PolicyCeiling` schema in openapi.yaml says what `policy_ceiling.py` accepts (S42-08b).

The schema is `additionalProperties: false`: a field the model has and the contract lacks makes a
client that validates against the contract reject a real server answer. This compares, section by
section, the property names, the numeric bounds and the defaults.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml
from choregos_core.policy_ceiling import PolicyCeiling

OPENAPI = pathlib.Path(__file__).resolve().parents[2] / "packages" / "contracts" / "openapi.yaml"
BOUNDS = ("minimum", "exclusiveMinimum", "maximum", "exclusiveMaximum", "maxLength")


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """The object schema behind a node: a `$ref`, or a nullable `anyOf` of one."""
    if "$ref" in node:
        return _resolve(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    for branch in node.get("anyOf", []):
        if branch.get("type") != "null":
            return _resolve(branch, defs)
    return node


def _bounds(node: dict[str, Any]) -> dict[str, Any]:
    found = {k: node[k] for k in BOUNDS if k in node}
    for branch in node.get("anyOf", []):
        found.update(_bounds(branch))
    return found


def _compare(path: str, contract: dict[str, Any], model: dict[str, Any], cdefs: dict, mdefs: dict) -> None:
    contract = _resolve(contract, cdefs)
    model = _resolve(model, mdefs)
    if "properties" not in model:
        return
    assert contract.get("additionalProperties") is False, path
    assert set(contract["properties"]) == set(model["properties"]), path
    assert set(contract.get("required", [])) == set(model.get("required", [])), path
    for name, prop in model["properties"].items():
        here = f"{path}.{name}"
        theirs = contract["properties"][name]
        assert _bounds(theirs) == _bounds(prop), here
        if "default" in prop and prop["default"] not in ({}, None):
            assert theirs.get("default") == prop["default"], here
        _compare(here, theirs, prop, cdefs, mdefs)


def test_policy_ceiling_contract_matches_the_model() -> None:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schemas = spec["components"]["schemas"]
    model = PolicyCeiling.model_json_schema()
    _compare("PolicyCeiling", schemas["PolicyCeiling"], model, schemas, model.get("$defs", {}))
