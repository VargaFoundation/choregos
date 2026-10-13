"""The agent-sandbox CRD schemas of the PINNED release, to validate what we write against them.

`crds-v1.0.6.json` is the storage version's `openAPIV3Schema` of each of the four CRDs of
`kubernetes-sigs/agent-sandbox` v1.0.6, extracted from the release's own GitOps assembly
(`infra/operators/agent-sandbox/`). An object the chart renders or the executor creates is
checked here the way the API server would check it — including the fields it would PRUNE
without a word: a misspelt `envVarsInjectionPolicy` is not an error for Kubernetes, it is a
setting silently dropped, so this validator refuses any field the schema does not declare.

Upgrading the operator means regenerating this file from the new release; the tests that load
it then say what moved.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

from jsonschema import Draft202012Validator

FIXTURE = pathlib.Path(__file__).with_name("crds-v1.0.6.json")


def pinned() -> dict[str, Any]:
    """`{"release": "v1.0.6", "kinds": {kind: {group, version, plural, scope, schema}}}`."""
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def _closed(schema: Any) -> Any:
    """The schema with `additionalProperties: false` wherever it lists properties and does not
    preserve unknown fields: the structural pruning of the API server, made an error."""
    if isinstance(schema, list):
        return [_closed(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    closed = {key: _closed(value) for key, value in schema.items()}
    if (
        "properties" in closed
        and "additionalProperties" not in closed
        and not closed.get("x-kubernetes-preserve-unknown-fields")
    ):
        closed["additionalProperties"] = False
    return closed


def problems(obj: dict[str, Any]) -> list[str]:
    """What the pinned CRD of `obj["kind"]` refuses or would prune in `obj`; empty if it fits."""
    kinds = pinned()["kinds"]
    kind = obj.get("kind", "")
    if kind not in kinds:
        return [f"unknown kind {kind!r} (pinned: {sorted(kinds)})"]
    spec = kinds[kind]
    expected = f"{spec['group']}/{spec['version']}"
    found: list[str] = []
    if obj.get("apiVersion") != expected:
        found.append(f"apiVersion {obj.get('apiVersion')!r}, the pinned CRD serves {expected!r}")
    schema = _closed(spec["schema"])
    # `metadata` is the API server's, not the CRD's: only its type is declared.
    schema.get("properties", {})["metadata"] = {"type": "object"}
    validator = Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path)):
        where = ".".join(str(part) for part in error.absolute_path) or "(root)"
        found.append(f"{where}: {error.message}")
    return found
