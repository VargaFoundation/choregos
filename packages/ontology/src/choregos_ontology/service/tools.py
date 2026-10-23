# SPDX-License-Identifier: Apache-2.0
"""The generated MCP tools, served to runs through the core's tool seam (trial, item 3).

The core announces these tools to the agent's `choregos-tools` MCP server (`GET /internal/runs/{id}/
tools`) and calls them for it (`POST /internal/runs/{id}/tools/{name}`), authenticated by the run's
token, under the per-run call cap, with a line in the cost ledger and a `tool.called` event. This
module sees neither the token nor the agent: it receives the run and its project, checked.

Only the tools the trial can serve are announced — an announced tool that answered "not yet" would be
a false promise made to the agent:
- `ontology_describe`, and `<type>_search`, `<type>_get` of the types with a `table` datasource;
- foreign-key links between two `table` types;
- `action_<name>` of the actions whose effects are all served (`actions.SERVED_EFFECTS`), which only
  **propose** (item 4), and `action_status`, `action_list`.
"""

from __future__ import annotations

from typing import Any

import jsonschema
from sqlalchemy.ext.asyncio import AsyncSession

from choregos_ontology.service import actions, objects
from choregos_ontology.service.objects import ToolRefusal

SERVED_KINDS = frozenset({"describe", "search", "get", "link", "action", "status", "list"})
PAGE = 20
CLEARANCE = objects.DEFAULT_CLEARANCE
#: How much of each kind's answer the agent reads (`result_max_bytes`, S40-17c), declared to the
#: core beside the tool's name. A page of objects (`search`, `link`, `list`, up to
#: `MAX_SEARCH_LIMIT` of them) and the description of a whole ontology are read whole up to 256 KiB:
#: past the core's 64 KiB default, a page cut in the middle loses its `next_cursor`. One object, one
#: proposal or its status stays at 64 KiB, whatever the deployment's default.
RESULT_CAPS: dict[str, int] = {
    "describe": 262144,
    "search": 262144,
    "link": 262144,
    "list": 262144,
    "get": 65536,
    "action": 65536,
    "status": 65536,
}
#: The key, in an announced tool, that tells the core's MCP gate the tool writes (read by
#: `choregos_api.mcp`: a protocol key, not prose).
WRITES_KEY = "ecriture"  # lang: fr-ok - read by the core's MCP gate


def _is_table(ir: dict[str, Any], type_name: str) -> bool:
    found = next((o for o in ir.get("object_types", []) if o["name"] == type_name), None)
    return found is not None and (found.get("datasource") or {}).get("type") == "table"


def _link_ends(ir: dict[str, Any], link_name: str) -> list[str]:
    link = next((lt for lt in ir.get("link_types", []) if lt["name"] == link_name), None)
    return [link["from"]["object_type"], link["to"]["object_type"]] if link else []


def _action_served(ir: dict[str, Any], name: str) -> bool:
    """Served to the agent: effects served by the trial, and the agent may propose it. A tool outside
    its rights is absent from the list and answers like a tool that does not exist (contract 03 §9)."""
    action = next((a for a in ir.get("action_types", []) if a["name"] == name), None)
    if action is None or f"agent:{actions.AGENT_IDENTITY}" not in action["propose"]:
        return False
    if action["cardinality"] != "none" and not _is_table(ir, action["target_type"]):
        return False
    return all(effect["type"] in actions.SERVED_EFFECTS for effect in action["effects"])


def served_tools(ir: dict[str, Any]) -> list[dict[str, Any]]:
    """The generated tools the trial serves, in the IR's format (`input_schema`)."""
    served = []
    for tool in ir.get("mcp_tools", []):
        kind, source = tool["kind"], tool.get("source")
        if kind not in SERVED_KINDS:
            continue
        if kind in {"search", "get"} and not _is_table(ir, source):
            continue
        if kind == "link" and not all(_is_table(ir, end) for end in _link_ends(ir, source) or [""]):
            continue
        if kind == "action" and not _action_served(ir, source):
            continue
        served.append(tool)
    return served


def describe(ir: dict[str, Any]) -> dict[str, Any]:
    """What the caller may see: types, readable properties, links and actions."""
    return {
        "ontology": {"name": ir.get("name"), "version": ir.get("version")},
        "object_types": [
            {
                "name": o["name"],
                "description": o.get("description") or o.get("display_name"),
                "primary_key": o.get("primary_key"),
                "properties": [
                    {k: p[k] for k in ("name", "type", "enum") if p.get(k) is not None}
                    for p in objects.visible_properties(o, CLEARANCE).values()
                ],
            }
            for o in ir.get("object_types", [])
            if o.get("mcp_exposed", True)
        ],
        "link_types": [
            {"name": lt["name"], "from": lt["from"]["object_type"], "to": lt["to"]["object_type"]}
            for lt in ir.get("link_types", [])
        ],
        "action_types": [
            {"name": a["name"], "description": a.get("description"), "risk": a.get("risk")}
            for a in ir.get("action_types", [])
            if a.get("mcp_exposed", True)
        ],
    }


async def list_tools(session: AsyncSession, run: Any, project: Any) -> list[dict[str, Any]]:
    version = await objects.active_version(session, project.id)
    if version is None:
        return []
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["input_schema"],
            "result_max_bytes": RESULT_CAPS[t["kind"]],
        }
        for t in served_tools(version.compiled_ir)
    ]


async def call_tool(
    session: AsyncSession, run: Any, project: Any, name: str, arguments: dict[str, Any]
) -> tuple[int, Any]:
    version = await objects.active_version(session, project.id)
    if version is None:
        return 404, {"error": "this project has no active ontology"}
    ir = version.compiled_ir
    tool = next((t for t in served_tools(ir) if t["name"] == name), None)
    if tool is None:
        return 404, {"error": f"unknown tool {name!r}"}
    try:
        jsonschema.validate(arguments, tool["input_schema"])
    except jsonschema.ValidationError as error:
        where = "/".join(str(p) for p in error.absolute_path) or "(root)"
        return 400, {"error": f"argument refused by the schema of {name} at {where}: {error.message}"}
    try:
        if tool["kind"] == "action":
            agent = actions.Actor(kind="agent", id=f"agent:{actions.AGENT_IDENTITY}", run_id=run.id)
            return await actions.propose(session, project, version, str(tool["source"]), arguments, agent)
        if tool["kind"] in {"status", "list"}:
            return 200, await _proposals(session, project, tool["kind"], arguments)
        return 200, await _dispatch(session, project.id, ir, tool, arguments)
    except ToolRefusal as refusal:
        return refusal.code, {"error": str(refusal)}
    except actions.Refusal as refusal:
        return refusal.code, refusal.body


async def _proposals(
    session: AsyncSession, project: Any, kind: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A proposal's status, or the list of them: core actions born from the ontology (S20-08)."""
    if kind == "status":
        try:
            proposal = await actions.get_action(session, project.id, str(arguments["proposal"]))
        except actions.Refusal as refusal:
            raise ToolRefusal(404, f"proposal {arguments['proposal']!r} not found") from refusal
        return actions.describe(proposal)
    rows = await actions.ontology_actions(session, project.id, str(arguments.get("status") or "") or None)
    if arguments.get("action_type"):
        rows = [r for r in rows if actions._meta(r).get("action_type") == str(arguments["action_type"])]
    start = int(arguments.get("cursor") or 0) if str(arguments.get("cursor") or "0").isdigit() else 0
    window = rows[start : start + PAGE]
    more = start + PAGE < len(rows)
    return {
        "proposals": [
            {
                "proposal": p.id,
                "action_type": actions._meta(p).get("action_type"),
                "status": p.status,
                "target": list(actions._meta(p).get("target_ids") or []),
            }
            for p in window
        ],
        "next_cursor": str(start + PAGE) if more else None,
    }


async def _dispatch(
    session: AsyncSession,
    project_id: str,
    ir: dict[str, Any],
    tool: dict[str, Any],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    kind, source = tool["kind"], str(tool.get("source") or "")
    if kind == "describe":
        return describe(ir)
    if kind == "search":
        target = objects.object_type(ir, source)
        return await objects.search(session, project_id, target, arguments, CLEARANCE)
    if kind == "get":
        target = objects.object_type(ir, source)
        return await objects.get(session, project_id, target, arguments["id"], CLEARANCE)
    # kind == "link": the name is `<owner type>_<end name>` (mcpgen.link_tool_name).
    link = next(lt for lt in ir["link_types"] if lt["name"] == source)
    owner = next(
        end["object_type"]
        for end in (link["from"], link["to"])
        if tool["name"] == f"{end['object_type']}_{end['name']}"
    )
    return await objects.linked(session, project_id, ir, source, owner, arguments, CLEARANCE)


# ───────────────────────── served to people, through the MCP gate (ADR 0030) ─────────────────────────
#
# The same ontology, served to a PERSON's MCP client — Claude Code, Cursor… — with THEIR rights: an
# action can be proposed if one of their roles is in `permissions.propose`, not because the platform
# lets its agent. It never decides: the gate has no authentication time, and `decide` refuses it by
# construction.


def _proposable_by(ir: dict[str, Any], name: str, rank: int) -> bool:
    action = next((a for a in ir.get("action_types", []) if a["name"] == name), None)
    if action is None:
        return False
    roles = [p.removeprefix("role:") for p in action["propose"] if p.startswith("role:")]
    if not any(rank >= actions.ONTOLOGY_ROLES.get(r, 3) for r in roles):
        return False
    if action["cardinality"] != "none" and not _is_table(ir, action["target_type"]):
        return False
    return all(effect["type"] in actions.SERVED_EFFECTS for effect in action["effects"])


def served_tools_for(ir: dict[str, Any], rank: int) -> list[dict[str, Any]]:
    """The tools served to a person of this rank: the reads, and the actions THEY may propose."""
    served = []
    for tool in ir.get("mcp_tools", []):
        kind, source = tool["kind"], tool.get("source")
        if kind not in SERVED_KINDS:
            continue
        if kind in {"search", "get"} and not _is_table(ir, source):
            continue
        if kind == "link" and not all(_is_table(ir, end) for end in _link_ends(ir, source) or [""]):
            continue
        if kind == "action" and not _proposable_by(ir, str(source), rank):
            continue
        served.append(tool)
    return served


async def list_tools_for_a_person(
    session: AsyncSession, principal: Any, project: Any, org_slug: str
) -> list[dict[str, Any]]:
    version = await objects.active_version(session, project.id)
    if version is None:
        return []
    rank = actions._rank(principal, org_slug, project.slug)
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["input_schema"],
            # Proposing an action is a write: only an `mcp:write` token sees it.
            WRITES_KEY: t["kind"] == "action",
        }
        for t in served_tools_for(version.compiled_ir, rank)
    ]


async def call_tool_for_a_person(
    session: AsyncSession, principal: Any, project: Any, org_slug: str, name: str, arguments: dict[str, Any]
) -> tuple[int, Any]:
    version = await objects.active_version(session, project.id)
    if version is None:
        return 404, {"error": "this project has no active ontology"}
    ir = version.compiled_ir
    rank = actions._rank(principal, org_slug, project.slug)
    tool = next((t for t in served_tools_for(ir, rank) if t["name"] == name), None)
    if tool is None:
        return 404, {"error": f"unknown tool {name!r}"}
    try:
        jsonschema.validate(arguments, tool["input_schema"])
    except jsonschema.ValidationError as error:
        where = "/".join(str(p) for p in error.absolute_path) or "(root)"
        return 400, {"error": f"argument refused by the schema of {name} at {where}: {error.message}"}
    try:
        if tool["kind"] == "action":
            actor = actions.Actor(kind="user", id=principal.email, user_id=principal.user_id, via="mcp")
            return await actions.propose(
                session, project, version, str(tool["source"]), arguments, actor, principal, org_slug
            )
        if tool["kind"] in {"status", "list"}:
            return 200, await _proposals(session, project, tool["kind"], arguments)
        return 200, await _dispatch(session, project.id, ir, tool, arguments)
    except ToolRefusal as refusal:
        return refusal.code, {"error": str(refusal)}
    except actions.Refusal as refusal:
        return refusal.code, refusal.body


async def pending_for_a_person(
    session: AsyncSession, principal: Any, project: Any, org_slug: str
) -> list[dict[str, Any]]:
    """The proposals waiting for a decision, with the console page where it is taken — the page of the
    core's actions, which they now are (S20-08)."""
    from choregos_api.services.actions import RANGS, rang

    pending = []
    for proposal in await actions.ontology_actions(session, project.id, actions.PENDING):
        approvers = (proposal.approval or {}).get("approvers") or [{"role": "project_owner"}]
        required = min(RANGS.get(str(a.get("role")), 3) for a in approvers)
        proposer = proposal.proposed_by or {}
        same_person = proposer.get("kind") == "user" and proposer.get("id") == principal.email
        meta = actions._meta(proposal)
        targets = ", ".join(meta.get("target_ids") or []) or "nothing"
        pending.append(
            {
                "key": proposal.id,
                "title": f"{meta.get('action_type')} on {targets}",
                "kind": "action proposal",
                "question": proposal.justification,
                "requested_at": proposal.created_at.isoformat() if proposal.created_at else None,
                "can_decide": rang(principal, org_slug, project.slug) >= required and not same_person,
                "decision_path": f"/p/{project.slug}/actions/{proposal.id}",
            }
        )
    return pending
