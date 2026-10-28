# SPDX-License-Identifier: Apache-2.0
"""The broker (ADR 0034): what a run reaches of the organisation's connectors, and how.

An agent never receives an MCP server's key or its address: it names a tool
(`<connector>__<operation>`), the `choregos-tools` side-car forwards it to `/internal`, and the
platform makes the call with THE CONNECTOR's key, resolved here. The run's token stays the only
credential in the pod; the server's key reaches the server, never the pod.

What a run sees is the intersection of four decisions:
- the SELECTION of its agent: the version names its servers (`mcp_servers`) and, where needed, tool
  patterns (ADR 0033) — an agent that does not name a connector sees nothing of it;
- the ACTIVATION by the organisation: the effective policy must be `allowed` — `approval` goes
  through a governed action (ADR 0035), `forbidden` does not exist for the run;
- the operation's GROUPS, which must intersect the project's (none: all);
- what the PROJECT tightens.

This module was `services/courtier.py` (ADR 0034, ADR 0046): that path is now a shim that
re-exports these names under their former ones, until S45 switches the importers.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AgentVersion,
    ConnectorOperation,
    OrgConnector,
    Project,
    ProjectOperationPolicy,
    Run,
)
from .catalogue_rights import SELECTED_PROJECTS, enabled_ids
from .connecteurs import declared_operation, human_floor, plus_stricte
from .platform_network import (
    WHERE,
    PlatformNetworkRefused,
    PlatformRateLimited,
    enforce_connector_call,
)
from .plugin_servers import connector_client

__all__ = [
    "SEPARATOR",
    "TRUNCATED",
    "ArgumentsRefused",
    "BrokerTool",
    "bounded_payload",
    "bounded_result",
    "broker_tools",
    "call",
    "call_id",
    "check_arguments",
    "output_refusal",
    "propose_call",
    "readable_text",
    "result_limit",
    "result_text",
    "selection_of_spec",
    "session_text",
    "tools_of_selection",
    "write_through_action",
]

SEPARATOR = "__"
#: The namespace of the ids of a write a run calls: the same call (run, tool, arguments) is the same
#: action — an agent that repeats it does not make the write twice.
_CALL_NAMESPACE = uuid.UUID("6b3f0f3e-6c0e-4f0a-9a52-0e3c8f1d2b41")


@dataclass(frozen=True)
class BrokerTool:
    name: str
    connector: OrgConnector
    operation: ConnectorOperation
    #: `approval`: the call PROPOSES a governed action (ADR 0035) instead of reaching the server.
    needs_approval: bool = False

    @property
    def is_write(self) -> bool:
        """A WRITE operation: even when allowed, it goes through a governed action (#241)."""
        return self.operation.access == "write"

    def announcement(self) -> dict[str, Any]:
        """The MCP format the side-car serves to the agent."""
        description = self.operation.description or self.operation.name
        if self.needs_approval:
            description += " — needs a human approval: calling it proposes the action, it does not run it"
        elif self.is_write:
            description += " — runs as a governed action: journaled, done once, undone if a later step fails"
        return {
            "name": self.name,
            "description": f"[{self.connector.name}] {description}",
            "inputSchema": self.operation.input_schema or {"type": "object"},
        }

    # former names, read by routers/internal.py and services/agent_sessions.py until S45 (ADR 0046)
    @property
    def nom(self) -> str:  # lang: fr-ok alias:internal
        return self.name

    @property
    def connecteur(self) -> OrgConnector:  # lang: fr-ok alias:internal
        return self.connector

    @property
    def sous_validation(self) -> bool:  # lang: fr-ok alias:internal
        return self.needs_approval

    @property
    def ecriture(self) -> bool:  # lang: fr-ok alias:internal
        return self.is_write

    def annonce(self) -> dict[str, Any]:  # lang: fr-ok alias:internal
        return self.announcement()


async def _selection_of_run(session: AsyncSession, run: Run) -> dict[str, list[str]]:
    """{connector: patterns} that the run's agent version names; nothing without a registry agent."""
    job = (run.stage_input or {}).get("job") if run.backend == "job" else None
    if isinstance(job, dict):
        # A job (ADR 0056) names its operations in the workflow (`tools:`), reviewed with it; the
        # orchestrator copied them into the run's StageInput, which the run cannot rewrite.
        return {str(c): [str(m) for m in patterns or []] for c, patterns in (job.get("tools") or {}).items()}
    if not run.agent_slug or not run.agent_version:
        return {}
    from ..db.models import Agent

    spec = (
        await session.execute(
            select(AgentVersion.spec)
            .join(Agent, Agent.id == AgentVersion.agent_id)
            .where(Agent.slug == run.agent_slug, AgentVersion.version == run.agent_version)
        )
    ).scalar_one_or_none()
    # As for the gate (S18-06): a server named without a pattern opens NOTHING; everything is `*`,
    # written — an agent version says what it takes, it does not take it by omission.
    return selection_of_spec(spec)


def selection_of_spec(spec: dict[str, Any] | None) -> dict[str, list[str]]:
    """{connector: patterns} a version's `mcp_servers` names (see `_selection_of_run`)."""
    selection: dict[str, list[str]] = {}
    for server in (spec or {}).get("mcp_servers") or []:
        if isinstance(server, dict) and server.get("connector"):
            selection[str(server["connector"])] = [str(m) for m in server.get("tools") or []]
    return selection


async def broker_tools(session: AsyncSession, run: Run, project: Project) -> dict[str, BrokerTool]:
    return await tools_of_selection(session, await _selection_of_run(session, run), project)


async def tools_of_selection(
    session: AsyncSession, selection: dict[str, list[str]], project: Project
) -> dict[str, BrokerTool]:
    """What a selection reaches in a project: the run's, or a session's agent's (S41-09)."""
    if not selection:
        return {}
    groups = {str(g) for g in ((project.config or {}).get("groups") or [])}
    rows = (
        await session.execute(
            select(ConnectorOperation, OrgConnector)
            .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
            .where(OrgConnector.org_id == project.org_id, OrgConnector.name.in_(list(selection)))
        )
    ).all()
    tightened = {
        p.operation_id: p.policy
        for p in (
            await session.execute(
                select(ProjectOperationPolicy).where(ProjectOperationPolicy.project_id == project.id)
            )
        ).scalars()
    }
    enabled = await enabled_ids(session, project)
    tools: dict[str, BrokerTool] = {}
    for operation, connector in rows:
        # ADR 0074: an organisation connector restricted to selected projects reaches only those.
        if connector.availability == SELECTED_PROJECTS and connector.id not in enabled:
            continue
        effective = human_floor(
            connector, operation.name, plus_stricte(operation.policy, tightened.get(operation.id))
        )
        if effective == "forbidden":
            continue
        if operation.groups and not groups & set(operation.groups):
            continue
        if not any(fnmatchcase(operation.name, m) for m in selection.get(connector.name, [])):
            continue
        name = f"{connector.name}{SEPARATOR}{operation.name}"
        tools[name] = BrokerTool(name, connector, operation, needs_approval=effective == "approval")
    return tools


async def propose_call(
    session: AsyncSession, run: Run, project: Project, tool: BrokerTool, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A tool under approval, called: a governed action, proposed in the name of the run's agent
    (`agent:<slug>`). Nothing reaches the server before a re-authenticated human approves it —
    and whoever owns the agent does not approve it (separation of roles)."""
    action = await _propose_tool_action(session, run, project, tool, arguments)
    return {
        "action": action.id,
        "status": action.status,
        "message": "proposed: a person approves it in the console before anything is done",
        "decision_path": f"/p/{project.slug}/actions/{action.id}",
    }


async def _propose_tool_action(
    session: AsyncSession,
    run: Run,
    project: Project,
    tool: BrokerTool,
    arguments: dict[str, Any],
    action_id: str | None = None,
) -> Any:
    """A tool call's action: a `connector.call` effect, in the name of the run's agent."""
    from choregos_contracts import ActionOrigin

    from ..rbac import SYSTEM
    from ..schemas.actions import ActionCreate, ActionEffectSpec, Approbateur, ApprovalSpec
    from .actions import proposer

    effect: dict[str, Any] = {
        "effect": "connector.call",
        "with": {
            "connector": tool.connector.name,
            "operation": tool.operation.name,
            "arguments": arguments,
        },
    }
    # What undoes it, as its type declares it (ADR 0052): a firewall rule changed by an action that
    # fails later is put back as it was.
    declared = declared_operation(tool.connector, tool.operation.name)
    if declared is not None and declared.compensation:
        effect["compensate"] = {
            "effect": "connector.call",
            "with": {"connector": tool.connector.name, **declared.compensation},
        }
    # Who approves (ADR 0052): the members of the group the organisation named for this operation —
    # with at least a developer's rank on the project —, otherwise the project's owners (ADR 0035).
    approval = (
        ApprovalSpec(approvers=[Approbateur(role="developer", group=tool.operation.approver_group)])
        if tool.operation.approver_group
        else ApprovalSpec()
    )
    body = ActionCreate(
        kind=f"{tool.connector.name}.{tool.operation.name}",
        title=f"{tool.operation.description or tool.operation.name} ({tool.connector.name})",
        justification=f"proposed by the agent {run.agent_slug} in run {run.id}",
        params={"arguments": arguments},
        effects=[ActionEffectSpec.model_validate(effect)],
        approval=approval,
        work_item_id=run.work_item_id,
    )
    return await proposer(
        session,
        project,
        body,
        origine=ActionOrigin.TOOL.value,
        propose_par={"kind": "agent", "id": f"agent:{run.agent_slug}", "run_id": run.id},
        principal=SYSTEM,
        run_id=run.id,
        action_id=action_id,
    )


def call_id(run_id: str, tool: str, arguments: dict[str, Any]) -> str:
    """The same call (run, tool, arguments) is the same action: replayed, it redoes nothing."""
    canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    return str(uuid.uuid5(_CALL_NAMESPACE, f"{run_id}:{tool}:{canonical}"))


async def write_through_action(
    session: AsyncSession, run: Run, project: Project, tool: BrokerTool, arguments: dict[str, Any]
) -> tuple[int, dict[str, Any], bool]:
    """An ALLOWED write, called by a run (#241, amends ADR 0034): a governed action, approved by the
    policy — journaled under its key, done once, compensated if a later step fails — and played by
    the `ActionWorkflow`, never by the request. The broker waits for its outcome, bounded
    (`courtier_attente_ecriture_s`), to hand the agent what the server answered; beyond that, 202
    and the action's id. The same call replayed returns the same action.

    Returns (code, body, created) — `created`: this call created the action (the price is counted once).
    """
    from choregos_contracts import ActionStatus

    from ..config import get_settings
    from ..db.models import Action
    from ..db.session import TOUT, limiter_aux_organisations, session_scope
    from ..temporal import action_id as temporal_action_id
    from ..temporal import get_temporal
    from .actions import approuvee_par_la_politique, politique_de_l_action

    action_id = call_id(run.id, tool.name, arguments)
    action = await session.get(Action, action_id)
    created = action is None
    if action is None:
        action = await _propose_tool_action(session, run, project, tool, arguments, action_id)
        worst, _reasons = await politique_de_l_action(session, project, action.effects, action.params or {})
        if worst != "allowed":
            # Tightened since the announcement (the organisation, the project): a person decides.
            return 202, _pending(project, action), created
        await approuvee_par_la_politique(session, project, action)
        # The row must be VISIBLE before the workflow reads it, and the wait below reads what the
        # worker commits: we commit here, then the run token's scope is set again — it only lives
        # for the length of one transaction.
        await session.commit()
        await limiter_aux_organisations(session, TOUT)
        await get_temporal().start_action(temporal_action_id(action.id), {"action_id": action.id})
    deadline = time.monotonic() + get_settings().courtier_attente_ecriture_s
    finished = {ActionStatus.SUCCEEDED.value, ActionStatus.FAILED.value, ActionStatus.REJECTED.value}
    while True:
        # One SHORT read per round: a transaction held open between two rounds would keep, under
        # SQLite, the lock that stops the worker from committing what it writes — the wait would
        # bound itself.
        async with session_scope(orgs=TOUT) as reading:
            current = await reading.get(Action, action_id)
            status = current.status if current is not None else action.status
        if status in finished or time.monotonic() >= deadline:
            break
        await asyncio.sleep(0.2)
    await session.refresh(action)
    if action.status == ActionStatus.SUCCEEDED.value:
        # The write is done: its result is masked and capped like a read's (#755), never refused —
        # a 422 would tell the agent that an effect that happened did not.
        result = await _action_result(session, action)
        return 200, bounded_result(result, tool.operation.result_max_bytes), created
    if action.status in finished:
        refusal = {"error": action.error or f"action {action.status}", "action": action.id}
        return 422, refusal, created
    if action.status == ActionStatus.PENDING_APPROVAL.value:
        return 202, _pending(project, action), created
    return (
        202,
        {
            "action": action.id,
            "status": action.status,
            "message": "the write runs as a governed action and is not finished yet: its result will be "
            "in the action's journal",
            "decision_path": f"/p/{project.slug}/actions/{action.id}",
        },
        created,
    )


def _pending(project: Project, action: Any) -> dict[str, Any]:
    return {
        "action": action.id,
        "status": action.status,
        "message": "proposed: a person approves it in the console before anything is done",
        "decision_path": f"/p/{project.slug}/actions/{action.id}",
    }


async def _action_result(session: AsyncSession, action: Any) -> dict[str, Any]:
    """What the write returned, in the format of an MCP result: the agent reads the same as from a
    direct call — plus the id of the action that made it."""
    from ..db.models import ActionEffect

    effect = (
        await session.execute(
            select(ActionEffect).where(ActionEffect.action_id == action.id, ActionEffect.position == 0)
        )
    ).scalar_one_or_none()
    returned: dict[str, Any] = dict(effect.result or {}) if effect is not None else {}
    if isinstance(returned.get("content"), list):
        return {**returned, "action": action.id}
    text = {"type": "text", "text": _json(returned)}
    return {"content": [text], "structuredContent": returned, "action": action.id}


class ArgumentsRefused(ValueError):  # noqa: N818 - a refusal with a reason, answered as a 400
    pass


def check_arguments(tool: BrokerTool, arguments: dict[str, Any]) -> None:
    """The announced schema is applied BEFORE the call: a paying provider does not receive a
    malformed call, and the registry does not count it."""
    if not tool.operation.input_schema:
        return
    import jsonschema

    try:
        jsonschema.validate(arguments, tool.operation.input_schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        raise ArgumentsRefused(
            f"argument refused by the schema of {tool.name} at {path}: {exc.message}"
        ) from exc


async def call(
    session: AsyncSession,
    tool: BrokerTool,
    arguments: dict[str, Any],
    *,
    run: Run | None = None,
    project: Project | None = None,
) -> tuple[int, dict[str, Any]]:
    """`tools/call` on the server, with the connector's key resolved HERE, never in the pod.

    Two halves of one network policy hold it (ADR 0070, amended): the organisation's network bounds
    are read FIRST, so a host its ceiling refuses (403) or that the organisation already called too
    often this minute (429) never receives the call; then every request runs under the
    organisation's egress allowlist (S39-22, ADR 0066), so a host it has not opened is a tool error
    that says so, not a timeout."""
    from .egress import egress_scope_for_org

    async with egress_scope_for_org(session, tool.connector.org_id, "api", f"mcp:{tool.connector.name}"):
        return await _call(session, tool, arguments, run=run, project=project)


async def _call(
    session: AsyncSession,
    tool: BrokerTool,
    arguments: dict[str, Any],
    *,
    run: Run | None = None,
    project: Project | None = None,
) -> tuple[int, dict[str, Any]]:
    import httpx
    from choregos_adapters.errors import AdapterError
    from choregos_adapters.mcp import ErreurMcp as McpError
    from choregos_core.redaction import redact
    from choregos_core.secrets import SecretIntrouvable

    instance = tool.connector
    try:
        await enforce_connector_call(
            session,
            instance,
            project=project,
            run_id=run.id if run else None,
            agent_slug=run.agent_slug if run else None,
        )
    except PlatformNetworkRefused as refused:
        return 403, {"error": f"{refused.message()}: {WHERE}", "type": "network_policy"}
    except PlatformRateLimited as rate_limited:
        return 429, {"error": str(rate_limited), "type": "rate_limited"}
    try:
        # A plugin's server (ADR 0096): its Service, its bearer token; any other: its type's adapter.
        client: Any = await connector_client(session, instance)
        if hasattr(client, "call_tool"):
            result = await client.call_tool(tool.operation.name, arguments)
        else:
            # A connector that is not MCP (a directory): its declared operation, by its name,
            # returned in the format of an MCP result — the agent reads the same everywhere.
            raw = await client.executer(tool.operation.name, arguments)
            result = {"content": [{"type": "text", "text": _json(raw)}], "structuredContent": raw}
    except McpError as mcp_error:
        return 502, {"error": redact(str(mcp_error))}
    except AdapterError as adapter_refusal:
        return 422, {"error": redact(str(adapter_refusal))}
    except (SecretIntrouvable, httpx.HTTPError) as failure:
        return 502, {"error": redact(f"{instance.name} unreachable: {failure}")}
    refusal = output_refusal(tool, result)
    if refusal is not None:
        return 422, {"error": refusal, "type": "output_schema"}
    # A job (ADR 0056) has no model to protect: a snapshot's seven years of bars are its input, and a
    # cut answer is no bars at all. It is masked, never cut (S40-13b).
    job = run is not None and run.backend == "job"
    return (200 if not result.get("isError") else 422), bounded_result(
        result, tool.operation.result_max_bytes, cut=not job
    )


#: The marker a capped result ends with: the agent knows it did not read everything.
TRUNCATED = "[truncated by the platform: {returned} of {total} bytes; ask for less]"


def output_refusal(tool: BrokerTool, result: dict[str, Any]) -> str | None:
    """Why a successful result breaks the `outputSchema` its server declared, or None (#755).

    A server that declares one and answers outside it is not answering the operation the
    organisation opened: the agent gets a 422 that names where, not the payload."""
    schema = tool.operation.output_schema
    if not schema or result.get("isError"):
        return None
    import jsonschema

    if "structuredContent" not in result:
        return (
            f"result of {tool.name} refused: its server declares an output schema"
            " and sent no structuredContent"
        )
    try:
        jsonschema.validate(result["structuredContent"], schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        # The rule broken, never `exc.message`: it quotes the value, and the value is the payload.
        rule = f"{exc.validator} {json.dumps(exc.validator_value, default=str)[:200]}"
        return f"result of {tool.name} refused by its output schema at {where}: fails `{rule}`"
    except jsonschema.SchemaError as exc:
        return f"result of {tool.name} refused: its server declares an invalid output schema ({exc.message})"
    return None


def result_limit(declared: int | None = None) -> int:
    """The cap a tool result is held to (S40-17): what its operation or its catalogue entry declares,
    else `broker_result_max_bytes` — and never above `broker_result_ceiling_bytes`."""
    from ..config import get_settings

    settings = get_settings()
    return min(declared or settings.broker_result_max_bytes, settings.broker_result_ceiling_bytes)


def bounded_result(
    result: dict[str, Any], declared: int | None = None, *, cut: bool = True
) -> dict[str, Any]:
    """A tool result as the agent may read it (#755): secrets masked in what it says, and no larger
    than `result_limit(declared)`. A larger one keeps its text, cut, with `TRUNCATED` at the end;
    its `structuredContent` is dropped, since half an object is not the object. `cut=False` masks
    only: a job reads the answer, not a model (S40-13b)."""
    masked = _masked(result)
    if not cut:
        return masked
    limit = result_limit(declared)
    total = len(_json(masked).encode("utf-8"))
    if total <= limit:
        return masked
    kept = {k: v for k, v in masked.items() if k not in ("content", "structuredContent")}
    return _cut(
        result_text(masked),
        lambda note: {**kept, "content": [{"type": "text", "text": note}]},
        total,
        limit,
    )


def bounded_payload(payload: Any, declared: int | None = None) -> Any:
    """Any tool answer as the agent may read it (#759): a catalogue tool's JSON, a plugin's body. An
    MCP result goes through `bounded_result`; anything else has its strings masked and, when larger
    than `result_limit(declared)`, becomes `{"text": <its JSON, cut, with TRUNCATED>, "truncated"}`."""
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        return bounded_result(payload, declared)
    masked = _masked_strings(payload)
    limit = result_limit(declared)
    total = len(_json(masked).encode("utf-8"))
    if total <= limit:
        return masked
    return _cut(_json(masked), lambda note: {"text": note}, total, limit)


def _cut(text: str, shape: Callable[[str], dict[str, Any]], total: int, limit: int) -> dict[str, Any]:
    """`shape(text cut + TRUNCATED)` with `truncated` beside it, no larger than `limit` as JSON."""
    whole = text.encode("utf-8")
    room = limit
    while True:
        kept = whole[:room].decode("utf-8", errors="ignore")
        returned = len(kept.encode("utf-8"))
        cut = {
            **shape(f"{kept}\n{TRUNCATED.format(returned=returned, total=total)}"),
            "truncated": {"returned_bytes": returned, "total_bytes": total},
        }
        # JSON escapes what it quotes: shrink by what is still over, until it fits.
        over = len(_json(cut).encode("utf-8")) - limit
        if over <= 0 or room == 0:
            return cut
        room = max(0, room - over)


def _masked_strings(value: Any) -> Any:
    """`redact` on every string of a JSON value, keys left as they are."""
    from choregos_core.redaction import redact

    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_masked_strings(v) for v in value]
    if isinstance(value, dict):
        return {k: _masked_strings(v) for k, v in value.items()}
    return value


def _masked(result: dict[str, Any]) -> dict[str, Any]:
    """`redact` on every text the agent reads: text blocks, embedded text resources, and the strings
    of `structuredContent`. Binary blocks (images, audio) are left as they are: base64 is not prose."""
    from choregos_core.redaction import redact

    def block(item: Any) -> Any:
        if not isinstance(item, dict):
            return item
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            return {**item, "text": redact(item["text"])}
        resource = item.get("resource")
        if (
            item.get("type") == "resource"
            and isinstance(resource, dict)
            and isinstance(resource.get("text"), str)
        ):
            return {**item, "resource": {**resource, "text": redact(resource["text"])}}
        return item

    masked = dict(result)
    if isinstance(masked.get("content"), list):
        masked["content"] = [block(b) for b in masked["content"]]
    if "structuredContent" in masked:
        masked["structuredContent"] = _masked_strings(masked["structuredContent"])
    return masked


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def result_text(result: dict[str, Any]) -> str:
    """What the agent will read: the text blocks of an MCP result."""
    blocks = result.get("content") or []
    return "\n".join(
        str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    )


def session_text(result: dict[str, Any]) -> str:
    """What a session's model reads of a tool answer (S40-18c): its text blocks, then its
    `structuredContent` as JSON unless a text block already serializes it, as MCP asks a server to
    do. The answer was bounded and masked before (`bounded_result`), its structured part with it."""
    text = result_text(result)
    structured = result.get("structuredContent")
    if structured is None or _serialized_in_text(result, structured):
        return text
    return f"{text}\n{_json(structured)}" if text else _json(structured)


def _serialized_in_text(result: dict[str, Any], structured: Any) -> bool:
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                if json.loads(str(block.get("text", ""))) == structured:
                    return True
            except ValueError:
                continue
    return False


def readable_text(payload: Any) -> str:
    """What the agent will read of any tool answer: an MCP result's text blocks and every string of
    its `structuredContent` (S40-18b), otherwise every string of the JSON, keys included — a
    catalogue provider or a plugin writes both (S40-18)."""
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        structured = payload.get("structuredContent")
        if structured is None:
            return result_text(payload)
        return f"{result_text(payload)}\n{readable_text(structured)}"
    strings: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, str):
            strings.append(value)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for k, v in value.items():
                strings.append(str(k))
                walk(v)

    walk(payload)
    return "\n".join(strings)
