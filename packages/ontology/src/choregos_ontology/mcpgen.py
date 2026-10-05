# SPDX-License-Identifier: Apache-2.0
"""Generation of the MCP tools from an ontology (contract 03 §9).

Pure functions: given the object types, links and actions of a package, they return the tool list,
names and JSON schemas included. Visibility per principal, and the calls themselves, belong to the
MCP server (SOC-062), not to this module.
"""

from __future__ import annotations

import re
from typing import Any

from choregos_ontology.model import ActionTypeSpec, LinkTypeSpec, ObjectTypeSpec, Property

TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
RESERVED_TOOLS = frozenset(
    {"ontology_describe", "action_status", "action_list", "sql_query", "dataset_search", "dataset_describe"}
    # Les outils de la porte MCP du cœur (ADR 0030) : une ontologie ne les masque pas.
    | {
        "list_projects",
        "describe_workflow",
        "create_work_item",
        "search_work_items",
        "get_work_item",
        "summarize_run",
        "list_pending_decisions",
    }
)
RESERVED_LINK_NAMES = frozenset({"search", "get", "aggregate", "describe"})
FILTER_OPERATORS = ["eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "prefix", "is_null"]
MAX_SEARCH_LIMIT = 100

_FORMATS = {"email": "email", "uri": "uri", "ipv4": "ipv4", "ipv6": "ipv6", "uuid": "uuid"}
_ARRAY = re.compile(r"^array<(.+)>$")
_REFERENCE = re.compile(r"^reference<([a-z][a-z0-9_]*)>$")
SCALAR_TYPES = frozenset({"string", "integer", "decimal", "boolean", "date", "timestamp", "duration"})
KNOWN_TYPES = SCALAR_TYPES | {"json", "geo_point"}


def is_known_type(type_: str) -> bool:
    if type_ in KNOWN_TYPES or _REFERENCE.match(type_):
        return True
    match = _ARRAY.match(type_)
    if match is None:
        return False
    return is_known_type(match.group(1))


def json_schema(type_: str, prop: Property | None = None) -> dict[str, Any]:
    """The JSON schema of an ontology type (contract 03 §2.3)."""
    schema: dict[str, Any]
    match = _ARRAY.match(type_)
    if match:
        schema = {"type": "array", "items": json_schema(match.group(1))}
    elif reference := _REFERENCE.match(type_):
        schema = {"type": "string", "description": f"id of a {reference.group(1)}"}
    elif type_ == "integer":
        schema = {"type": "integer"}
    elif type_ == "decimal":
        schema = {"type": "number"}
    elif type_ == "boolean":
        schema = {"type": "boolean"}
    elif type_ == "date":
        schema = {"type": "string", "format": "date"}
    elif type_ == "timestamp":
        schema = {"type": "string", "format": "date-time"}
    elif type_ == "duration":
        schema = {"type": "string", "format": "duration"}
    elif type_ == "geo_point":
        schema = {"type": "object", "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}}}
    elif type_ == "json":
        schema = {}
    else:
        schema = {"type": "string"}
    if prop is not None:
        if prop.enum is not None:
            schema["enum"] = list(prop.enum)
        if prop.format in _FORMATS:
            schema["format"] = _FORMATS[prop.format]
        if prop.description:
            schema["description"] = prop.description
    return schema


def parameters_schema(parameters: dict[str, Property]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: json_schema(p.type, p) for name, p in sorted(parameters.items())},
        "required": sorted(name for name, p in parameters.items() if p.required),
        "additionalProperties": False,
    }


def search_tool(name: str, spec: ObjectTypeSpec, *, semantic: bool) -> dict[str, Any]:
    filterable = sorted(p for p, prop in spec.properties.items() if prop.filterable)
    sortable = sorted(p for p, prop in spec.properties.items() if prop.sortable)
    properties: dict[str, Any] = {
        "filters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "property": {"type": "string", "enum": filterable},
                    "op": {"type": "string", "enum": FILTER_OPERATORS},
                    "value": {},
                },
                "required": ["property", "op"],
                "additionalProperties": False,
            },
        },
        "sort": {
            "type": "object",
            "properties": {
                "property": {"type": "string", "enum": sortable},
                "order": {"type": "string", "enum": ["asc", "desc"]},
            },
            "required": ["property"],
            "additionalProperties": False,
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SEARCH_LIMIT, "default": 20},
        "cursor": {"type": "string"},
    }
    if semantic:
        properties["query"] = {"type": "string", "description": "semantic search on the declared fields"}
    return {"type": "object", "properties": properties, "additionalProperties": False}


def action_tool_schema(spec: ActionTypeSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "justification": {"type": "string", "minLength": 1, "description": "why the agent proposes it"},
        "params": parameters_schema(spec.parameters),
    }
    required = ["justification", "params"]
    if spec.target.cardinality != "none":
        target: dict[str, Any] = {"type": "array", "items": {"type": "string"}, "minItems": 1}
        if spec.target.cardinality == "one":
            target["maxItems"] = 1
        properties["target"] = target
        required.insert(0, "target")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def link_tool_name(owner: str, end_name: str) -> str:
    return f"{owner}_{end_name}"


def action_tool_name(action: str) -> str:
    return f"action_{action.replace('.', '_')}"


def plan_tool_names(
    objects: dict[str, ObjectTypeSpec], links: dict[str, LinkTypeSpec], actions: dict[str, ActionTypeSpec]
) -> list[tuple[str, str, str]]:
    """Every generated tool name with its kind and source, before any validation (for ONT025)."""
    names: list[tuple[str, str, str]] = []
    for name in sorted(objects):
        names += [(f"{name}_search", "search", name), (f"{name}_get", "get", name)]
    for name in sorted(links):
        spec = links[name]
        names.append((link_tool_name(spec.from_.object_type, spec.from_.name), "link", name))
        names.append((link_tool_name(spec.to.object_type, spec.to.name), "link", name))
    for name in sorted(actions):
        names.append((action_tool_name(name), "action", name))
    return names
