# SPDX-License-Identifier: Apache-2.0
"""Read the objects of a `table` datasource: search, read by id, links (contract 03 §9).

The rules that matter to an agent:
- a property above the caller's clearance (default `internal`, R-SOC-AUT-03) is removed from the
  results **and** treated as nonexistent in filters and sorting — otherwise its value could be
  inferred from a filter;
- 100 objects per page at most (R-SOC-MCP-05), opaque cursor;
- an object type whose datasource is not `table` is not served by the trial, and it says so.

Trial simplification: filters are evaluated in Python over the type's objects, loaded for the project.
The spec asks for expression indexes on PostgreSQL (R-SOC-ONT-16); the trial's volume (a few hundred
objects) measures nothing on that side.
"""

from __future__ import annotations

import base64
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from choregos_ontology.mcpgen import MAX_SEARCH_LIMIT
from choregos_ontology.service.store import ACTIVE, ManagedObject, OntologyVersion

SENSITIVITIES = ("public", "internal", "confidential", "secret")
DEFAULT_CLEARANCE = "internal"
DEFAULT_LIMIT = 20


class ToolRefusal(Exception):  # noqa: N818 - an answer for the agent, not a failure
    """The call is refused; `code` is the HTTP status returned to the agent with the message."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


async def active_version(session: AsyncSession, project_id: str) -> OntologyVersion | None:
    return (
        await session.execute(
            select(OntologyVersion).where(
                OntologyVersion.project_id == project_id, OntologyVersion.status == ACTIVE
            )
        )
    ).scalar_one_or_none()


def object_type(ir: dict[str, Any], name: str) -> dict[str, Any]:
    for candidate in ir.get("object_types", []):
        if candidate["name"] == name:
            return dict(candidate)
    raise ToolRefusal(404, f"unknown object type {name!r}")


def visible_properties(obj_type: dict[str, Any], clearance: str = DEFAULT_CLEARANCE) -> dict[str, Any]:
    """{name: IR property} readable with this clearance."""
    ceiling = SENSITIVITIES.index(clearance)
    return {
        p["name"]: p
        for p in obj_type.get("properties", [])
        if SENSITIVITIES.index(p.get("sensitivity") or DEFAULT_CLEARANCE) <= ceiling
    }


def _require_table(obj_type: dict[str, Any]) -> None:
    kind = (obj_type.get("datasource") or {}).get("type")
    if kind != "table":
        raise ToolRefusal(
            501, f"{obj_type['name']}: datasource {kind!r} is not served by the trial (only `table`)"
        )


async def _rows(session: AsyncSession, project_id: str, type_name: str) -> list[ManagedObject]:
    return list(
        (
            await session.execute(
                select(ManagedObject)
                .where(
                    ManagedObject.project_id == project_id,
                    ManagedObject.object_type == type_name,
                    ManagedObject.deleted_at.is_(None),
                )
                .order_by(ManagedObject.id)
            )
        )
        .scalars()
        .all()
    )


def _view(row: ManagedObject, visible: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (row.properties or {}).items() if k in visible}


def _matches(value: Any, op: str, expected: Any) -> bool:
    if op == "is_null":
        return (value is None) == bool(expected if expected is not None else True)
    if value is None:
        return op == "ne" and expected is not None
    try:
        if op == "eq":
            return bool(value == expected)
        if op == "ne":
            return bool(value != expected)
        if op == "lt":
            return bool(value < expected)
        if op == "lte":
            return bool(value <= expected)
        if op == "gt":
            return bool(value > expected)
        if op == "gte":
            return bool(value >= expected)
    except TypeError as error:
        raise ToolRefusal(400, f"cannot compare {value!r} with {expected!r} ({op})") from error
    if op == "in":
        if not isinstance(expected, list):
            raise ToolRefusal(400, "`in` expects a list")
        return value in expected
    if op == "contains":
        if isinstance(value, list):
            return expected in value
        return isinstance(value, str) and isinstance(expected, str) and expected in value
    if op == "prefix":
        return isinstance(value, str) and isinstance(expected, str) and value.startswith(expected)
    raise ToolRefusal(400, f"unknown operator {op!r}")


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"o:{offset}".encode()).decode()


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        if not decoded.startswith("o:"):
            raise ValueError(decoded)
        offset = int(decoded[2:])
    except ValueError as error:
        raise ToolRefusal(400, "invalid cursor") from error
    if offset < 0:
        raise ToolRefusal(400, "invalid cursor")
    return offset


def _page(objects: list[dict[str, Any]], arguments: dict[str, Any]) -> dict[str, Any]:
    limit = int(arguments.get("limit") or DEFAULT_LIMIT)
    if not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise ToolRefusal(400, f"limit must be between 1 and {MAX_SEARCH_LIMIT}")
    start = _offset(arguments.get("cursor"))
    window = objects[start : start + limit]
    more = start + limit < len(objects)
    return {"objects": window, "next_cursor": _cursor(start + limit) if more else None}


async def search(
    session: AsyncSession,
    project_id: str,
    obj_type: dict[str, Any],
    arguments: dict[str, Any],
    clearance: str = DEFAULT_CLEARANCE,
) -> dict[str, Any]:
    _require_table(obj_type)
    visible = visible_properties(obj_type, clearance)
    filterable = {n for n, p in visible.items() if p.get("filterable", True)}
    sortable = {n for n, p in visible.items() if p.get("sortable", True)}
    filters = list(arguments.get("filters") or [])
    for condition in filters:
        if condition.get("property") not in filterable:
            # A property above the clearance is "nonexistent", like an unknown one.
            raise ToolRefusal(400, f"unknown or non-filterable property {condition.get('property')!r}")
    sort = arguments.get("sort")
    if sort is not None and sort.get("property") not in sortable:
        raise ToolRefusal(400, f"unknown or non-sortable property {sort.get('property')!r}")
    objects = [_view(row, visible) for row in await _rows(session, project_id, obj_type["name"])]
    for condition in filters:
        name, op, expected = condition["property"], condition["op"], condition.get("value")
        objects = [o for o in objects if _matches(o.get(name), op, expected)]
    if sort is not None:
        key = sort["property"]
        present = sorted((o for o in objects if o.get(key) is not None), key=lambda o: o[key])
        if sort.get("order") == "desc":
            present.reverse()
        objects = present + [o for o in objects if o.get(key) is None]
    return _page(objects, arguments)


async def get(
    session: AsyncSession,
    project_id: str,
    obj_type: dict[str, Any],
    object_id: str,
    clearance: str = DEFAULT_CLEARANCE,
) -> dict[str, Any]:
    _require_table(obj_type)
    row = await session.get(ManagedObject, (project_id, obj_type["name"], object_id))
    if row is None or row.deleted_at is not None:
        raise ToolRefusal(404, f"{obj_type['name']} {object_id!r} not found")
    return {"object": _view(row, visible_properties(obj_type, clearance))}


async def linked(
    session: AsyncSession,
    project_id: str,
    ir: dict[str, Any],
    link_name: str,
    owner_type: str,
    arguments: dict[str, Any],
    clearance: str = DEFAULT_CLEARANCE,
) -> dict[str, Any]:
    """The objects linked to `arguments.id` by a foreign-key link, seen from `owner_type`."""
    link = next((lt for lt in ir.get("link_types", []) if lt["name"] == link_name), None)
    if link is None:
        raise ToolRefusal(404, f"unknown link {link_name!r}")
    join = link.get("join") or {}
    if join.get("kind") != "foreign_key":
        raise ToolRefusal(501, f"{link_name}: only foreign-key links are served by the trial")
    ends = {"from": link["from"]["object_type"], "to": link["to"]["object_type"]}
    if owner_type not in ends.values():
        raise ToolRefusal(404, f"{link_name} does not start from {owner_type!r}")
    if ends["from"] == ends["to"]:
        raise ToolRefusal(501, f"{link_name}: self links are not served by the trial")
    other_side = "to" if ends["from"] == owner_type else "from"
    owner = object_type(ir, owner_type)
    other = object_type(ir, ends[other_side])
    _require_table(owner)
    _require_table(other)
    source = await session.get(ManagedObject, (project_id, owner_type, str(arguments["id"])))
    if source is None or source.deleted_at is not None:
        raise ToolRefusal(404, f"{owner_type} {arguments['id']!r} not found")
    fk_side, fk = join["side"], join["property"]
    visible = visible_properties(other, clearance)
    rows = await _rows(session, project_id, other["name"])
    if fk_side == other_side:
        # The key is on the other end: its objects whose `fk` is the source's id.
        objects = [_view(r, visible) for r in rows if (r.properties or {}).get(fk) == source.id]
    else:
        # The key is on the source: the object it names.
        target = (source.properties or {}).get(fk)
        objects = [_view(r, visible) for r in rows if r.id == target]
    return _page(objects, arguments)
