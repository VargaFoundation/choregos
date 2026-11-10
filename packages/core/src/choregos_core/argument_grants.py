# SPDX-License-Identifier: Apache-2.0
"""A tool grant may bind the call's arguments (S60-06, ADR 0128).

Allowing an agent to `sync` every application of an Argo CD is too broad; allowing it on one is fine.
A grant therefore may carry a JSON Schema the call's arguments must ALSO satisfy, on top of the
operation's own `input_schema`. This module holds what does not need the database: whether a
constraint may be written, which placeholders it carries, which properties it names, and whether a
call's arguments satisfy it.

The schema is the one dialect the platform reads (JSON Schema 2020-12) with nothing remote and
nothing indirect: no `$ref`, no `$dynamicRef`, no `$id`, so validating it never reads anything but
the schema and the arguments. Its size is bounded. Only `{project}` is a placeholder (the group
placeholder of S59-01, `choregos_core.dsl.groups`); it stands for the run's project slug and is
replaced in string values before the arguments are checked.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema

from .dsl.groups import PROJECT

#: The most a constraint weighs as JSON: it is stored in a version, a rule and a policy, and announced.
MAX_CONSTRAINT_BYTES = 8 * 1024
#: Keywords that make a schema read anything beyond itself.
_INDIRECT = frozenset({"$ref", "$dynamicRef", "$recursiveRef", "$id", "$anchor", "$dynamicAnchor"})
#: The schema keywords whose value is a sub-schema or a list of them, searched for the properties named.
_COMBINATORS = ("allOf", "anyOf", "oneOf")


def constraint_error(schema: object) -> str | None:
    """Why `schema` may not be written as an argument constraint, or `None` when it may."""
    if not isinstance(schema, dict):
        return "an argument constraint is a JSON Schema object"
    if len(json.dumps(schema, default=str)) > MAX_CONSTRAINT_BYTES:
        return f"an argument constraint holds at most {MAX_CONSTRAINT_BYTES // 1024} KiB of JSON"
    found = sorted(_indirect_keywords(schema))
    if found:
        return f"an argument constraint reads nothing but itself: {', '.join(found)} is refused"
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        return f"not a valid JSON Schema (2020-12): {exc.message}"
    return None


def _indirect_keywords(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        found |= _INDIRECT & set(node)
        for value in node.values():
            found |= _indirect_keywords(value)
    elif isinstance(node, list):
        for value in node:
            found |= _indirect_keywords(value)
    return found


def properties_named(schema: dict[str, Any]) -> set[str]:
    """The argument names a constraint speaks of: its `properties`, its `required`, and the same
    inside `allOf`, `anyOf` and `oneOf`. A name the operation does not have can only be a typo: the
    constraint would bind nothing, or refuse every call."""
    named = set(schema.get("properties") or {}) | {str(n) for n in schema.get("required") or []}
    for keyword in _COMBINATORS:
        for sub in schema.get(keyword) or []:
            if isinstance(sub, dict):
                named |= properties_named(sub)
    return named


def interpolate(schema: Any, project_slug: str) -> Any:
    """The constraint for one project: `{project}` replaced by its slug in every string value (a
    property name is the operation's, never a placeholder)."""
    if isinstance(schema, str):
        return schema.replace(PROJECT, project_slug)
    if isinstance(schema, list):
        return [interpolate(item, project_slug) for item in schema]
    if isinstance(schema, dict):
        return {key: interpolate(value, project_slug) for key, value in schema.items()}
    return schema


def violation(schema: dict[str, Any], arguments: dict[str, Any]) -> tuple[str, str] | None:
    """`(JSON path, message)` of the first place `arguments` leave `schema`, or `None`."""
    validator = jsonschema.Draft202012Validator(schema)
    error = jsonschema.exceptions.best_match(validator.iter_errors(arguments))
    if error is None:
        return None
    return "/" + "/".join(str(p) for p in error.absolute_path), error.message


def narrowed(input_schema: dict[str, Any] | None, constraints: list[dict[str, Any]]) -> dict[str, Any]:
    """What a run is told: the operation's schema and every constraint, together (`allOf`)."""
    base = input_schema or {"type": "object"}
    if not constraints:
        return base
    # `type: object` stays at the top: an MCP client reads a tool's schema as an object schema.
    return {"type": "object", "allOf": [base, *constraints]}


__all__ = [
    "MAX_CONSTRAINT_BYTES",
    "constraint_error",
    "interpolate",
    "narrowed",
    "properties_named",
    "violation",
]
