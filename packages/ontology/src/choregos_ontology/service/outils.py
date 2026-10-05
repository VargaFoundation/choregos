# SPDX-License-Identifier: Apache-2.0
"""Les outils MCP générés, servis aux runs par la couture d'outils du cœur (essai, élément 3).

Le cœur annonce ces outils au serveur MCP `choregos-tools` de l'agent (`GET /internal/runs/{id}/
tools`) et les appelle pour lui (`POST /internal/runs/{id}/tools/{nom}`), authentifié par le jeton
du run, sous le plafond d'appels par run, avec une ligne au registre des coûts et un événement
`tool.called`. Ce module ne voit ni le jeton ni l'agent : il reçoit le run et son projet, vérifiés.

Seuls les outils que l'essai sait servir sont annoncés — un outil annoncé qui répondrait « pas
encore » serait une promesse fausse faite à l'agent :
- `ontology_describe`, et `<type>_search`, `<type>_get` des types à datasource `table` ;
- les liens à clé étrangère entre deux types `table` ;
- `action_<nom>` des actions dont tous les effets sont servis (`actions.SERVED_EFFECTS`), qui ne
  font que **proposer** (élément 4), et `action_status`, `action_list`.
"""

from __future__ import annotations

from typing import Any

import jsonschema
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from choregos_ontology.service import actions, objects
from choregos_ontology.service.objects import ToolRefusal
from choregos_ontology.service.store import ActionProposal

SERVED_KINDS = frozenset({"describe", "search", "get", "link", "action", "status", "list"})
PAGE = 20
CLEARANCE = objects.DEFAULT_CLEARANCE


def _is_table(ir: dict[str, Any], type_name: str) -> bool:
    found = next((o for o in ir.get("object_types", []) if o["name"] == type_name), None)
    return found is not None and (found.get("datasource") or {}).get("type") == "table"


def _link_ends(ir: dict[str, Any], link_name: str) -> list[str]:
    link = next((lt for lt in ir.get("link_types", []) if lt["name"] == link_name), None)
    return [link["from"]["object_type"], link["to"]["object_type"]] if link else []


def _action_served(ir: dict[str, Any], name: str) -> bool:
    """Servie à l'agent : effets servis par l'essai, et l'agent a le droit de la proposer. Un outil
    hors droits est absent de la liste et répond comme un outil inexistant (contrat 03 §9)."""
    action = next((a for a in ir.get("action_types", []) if a["name"] == name), None)
    if action is None or f"agent:{actions.AGENT_IDENTITY}" not in action["propose"]:
        return False
    if action["cardinality"] != "none" and not _is_table(ir, action["target_type"]):
        return False
    return all(effect["type"] in actions.SERVED_EFFECTS for effect in action["effects"])


def served_tools(ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Les outils générés que l'essai sert, dans le format de l'IR (`input_schema`)."""
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
    """Ce que l'appelant peut voir : types, propriétés lisibles, liens et actions."""
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


async def lister(session: AsyncSession, run: Any, project: Any) -> list[dict[str, Any]]:
    version = await objects.active_version(session, project.id)
    if version is None:
        return []
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
        for t in served_tools(version.compiled_ir)
    ]


async def appeler(
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
    if kind == "status":
        proposal = await session.get(ActionProposal, str(arguments["proposal"]))
        if proposal is None or proposal.project_id != project.id:
            raise ToolRefusal(404, f"proposal {arguments['proposal']!r} not found")
        await actions.expire(session, project, proposal)
        return actions.describe(proposal)
    query = select(ActionProposal).where(ActionProposal.project_id == project.id)
    if arguments.get("status"):
        query = query.where(ActionProposal.status == str(arguments["status"]))
    if arguments.get("action_type"):
        query = query.where(ActionProposal.action_type == str(arguments["action_type"]))
    rows = list(
        (await session.execute(query.order_by(ActionProposal.created_at, ActionProposal.id))).scalars()
    )
    start = int(arguments.get("cursor") or 0) if str(arguments.get("cursor") or "0").isdigit() else 0
    window = rows[start : start + PAGE]
    more = start + PAGE < len(rows)
    return {
        "proposals": [
            {"proposal": p.id, "action_type": p.action_type, "status": p.status, "target": list(p.target_ids)}
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
    # kind == "link" : le nom est `<type propriétaire>_<nom du bout>` (mcpgen.link_tool_name).
    link = next(lt for lt in ir["link_types"] if lt["name"] == source)
    owner = next(
        end["object_type"]
        for end in (link["from"], link["to"])
        if tool["name"] == f"{end['object_type']}_{end['name']}"
    )
    return await objects.linked(session, project_id, ir, source, owner, arguments, CLEARANCE)


# ───────────────────────────── servis aux humains, par la porte MCP (ADR 0030) ─────────────────────────────
#
# La même ontologie, servie au client MCP d'une PERSONNE — Claude Code, Cursor… — avec SES droits :
# une action se propose si l'un de ses rôles figure dans `permissions.propose`, pas parce que la
# plateforme le permet à son agent. Elle ne décide jamais : la porte n'a pas d'heure
# d'authentification, et `decide` le refuse par construction.


def _proposable_par(ir: dict[str, Any], name: str, rang: int) -> bool:
    action = next((a for a in ir.get("action_types", []) if a["name"] == name), None)
    if action is None:
        return False
    roles = [p.removeprefix("role:") for p in action["propose"] if p.startswith("role:")]
    if not any(rang >= actions.ONTOLOGY_ROLES.get(r, 3) for r in roles):
        return False
    if action["cardinality"] != "none" and not _is_table(ir, action["target_type"]):
        return False
    return all(effect["type"] in actions.SERVED_EFFECTS for effect in action["effects"])


def served_tools_for(ir: dict[str, Any], rang: int) -> list[dict[str, Any]]:
    """Les outils servis à un humain de ce rang : les lectures, et les actions qu'IL peut proposer."""
    served = []
    for tool in ir.get("mcp_tools", []):
        kind, source = tool["kind"], tool.get("source")
        if kind not in SERVED_KINDS:
            continue
        if kind in {"search", "get"} and not _is_table(ir, source):
            continue
        if kind == "link" and not all(_is_table(ir, end) for end in _link_ends(ir, source) or [""]):
            continue
        if kind == "action" and not _proposable_par(ir, str(source), rang):
            continue
        served.append(tool)
    return served


async def lister_pour_un_humain(
    session: AsyncSession, principal: Any, project: Any, org_slug: str
) -> list[dict[str, Any]]:
    version = await objects.active_version(session, project.id)
    if version is None:
        return []
    rang = actions._rank(principal, org_slug, project.slug)
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "inputSchema": t["input_schema"],
            # Proposer une action est une écriture : seul un jeton `mcp:write` la voit.
            "ecriture": t["kind"] == "action",
        }
        for t in served_tools_for(version.compiled_ir, rang)
    ]


async def appeler_pour_un_humain(
    session: AsyncSession, principal: Any, project: Any, org_slug: str, name: str, arguments: dict[str, Any]
) -> tuple[int, Any]:
    version = await objects.active_version(session, project.id)
    if version is None:
        return 404, {"error": "this project has no active ontology"}
    ir = version.compiled_ir
    rang = actions._rank(principal, org_slug, project.slug)
    tool = next((t for t in served_tools_for(ir, rang) if t["name"] == name), None)
    if tool is None:
        return 404, {"error": f"unknown tool {name!r}"}
    try:
        jsonschema.validate(arguments, tool["input_schema"])
    except jsonschema.ValidationError as error:
        where = "/".join(str(p) for p in error.absolute_path) or "(root)"
        return 400, {"error": f"argument refused by the schema of {name} at {where}: {error.message}"}
    try:
        if tool["kind"] == "action":
            acteur = actions.Actor(kind="user", id=principal.email, user_id=principal.user_id, via="mcp")
            return await actions.propose(
                session, project, version, str(tool["source"]), arguments, acteur, principal, org_slug
            )
        if tool["kind"] in {"status", "list"}:
            return 200, await _proposals(session, project, tool["kind"], arguments)
        return 200, await _dispatch(session, project.id, ir, tool, arguments)
    except ToolRefusal as refusal:
        return refusal.code, {"error": str(refusal)}
    except actions.Refusal as refusal:
        return refusal.code, refusal.body


async def en_attente_pour_un_humain(
    session: AsyncSession, principal: Any, project: Any, org_slug: str
) -> list[dict[str, Any]]:
    """Les propositions qui attendent une décision, avec la page de la console où elle se prend."""
    rows = (
        await session.execute(
            select(ActionProposal)
            .where(ActionProposal.project_id == project.id, ActionProposal.status == actions.PENDING)
            .order_by(ActionProposal.created_at)
        )
    ).scalars()
    rang = actions._rank(principal, org_slug, project.slug)
    attentes = []
    for proposal in rows:
        approvers = (proposal.approval or {}).get("approvers") or []
        requis = max((actions.ONTOLOGY_ROLES.get(str(a.get("role")), 3) for a in approvers), default=2)
        proposant = proposal.proposed_by or {}
        meme_personne = proposant.get("kind") == "user" and proposant.get("id") == principal.email
        attentes.append(
            {
                "key": proposal.id,
                "title": f"{proposal.action_type} on {', '.join(proposal.target_ids) or 'nothing'}",
                "kind": "action proposal",
                "question": proposal.justification,
                "requested_at": proposal.created_at.isoformat() if proposal.created_at else None,
                "can_decide": rang >= requis and not meme_personne,
                "decision_path": f"/p/{project.slug}/proposals/{proposal.id}",
            }
        )
    return attentes
