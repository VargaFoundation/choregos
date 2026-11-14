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

This module was `services/courtier.py` (ADR 0034, ADR 0046); the former path is gone.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any, Literal

from choregos_core.argument_grants import interpolate, narrowed, violation
from choregos_core.mcp import data_once, serializes
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
from ..db.session import outside_the_transaction
from .catalogue_rights import SELECTED_PROJECTS, enabled_ids
from .connected_accounts import NOT_CONNECTED, AccountNotConnected, consented_token
from .connectors import declared_operation, human_floor, stricter
from .identity_exchange import (
    UNAVAILABLE,
    USER_CONSENT,
    USER_EXCHANGE,
    Acting,
    IdentityUnavailable,
    exchanged_token,
)
from .mcp_gateway import RouteRefusal, connector_client, route_of, tool_error
from .mcp_gateway_identity import identity_of_run
from .platform_network import (
    WHERE,
    PlatformNetworkRefused,
    PlatformRateLimited,
    enforce_connector_call,
)

__all__ = [
    "SEPARATOR",
    "TRUNCATED",
    "ArgumentConstraint",
    "ArgumentsOutsideGrant",
    "ArgumentsRefused",
    "BrokerTool",
    "bounded_payload",
    "bounded_result",
    "broker_tools",
    "call",
    "call_id",
    "check_arguments",
    "is_job_step",
    "output_refusal",
    "propose_call",
    "proposer_of",
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
class ArgumentConstraint:
    """What one level binds of a call's arguments (S60-06, ADR 0128): `agent` (the version's grant),
    `organisation` (the operation's rule) or `project` (what the project narrows). `schema` has its
    `{project}` placeholders already replaced."""

    level: Literal["agent", "organisation", "project"]
    schema: dict[str, Any]


@dataclass(frozen=True)
class BrokerTool:
    name: str
    connector: OrgConnector
    operation: ConnectorOperation
    #: `approval`: the call PROPOSES a governed action (ADR 0035) instead of reaching the server.
    needs_approval: bool = False
    #: Every level's constraint on the call's arguments: ALL must pass, on top of the operation's own
    #: input schema (intersection, S60-06).
    constraints: tuple[ArgumentConstraint, ...] = ()

    @property
    def is_write(self) -> bool:
        """A WRITE operation: even when allowed, it goes through a governed action (#241)."""
        return self.operation.access == "write"

    @property
    def access(self) -> Literal["read", "write", "approval"]:
        """What calling it does (ADR 0122): `approval` proposes an action a person decides — a read
        under approval too —, `write` plays a governed action, `read` reads."""
        if self.needs_approval:
            return "approval"
        return "write" if self.is_write else "read"

    def announcement(self) -> dict[str, Any]:
        """The MCP format the side-car serves to the agent. `readOnlyHint` is MCP's own annotation:
        true only when a call reads, never for a proposal, which writes into the inbox."""
        description = self.operation.description or self.operation.name
        if self.needs_approval:
            description += " — needs a human approval: calling it proposes the action, it does not run it"
        elif self.is_write:
            description += " — runs as a governed action: journaled, done once, undone if a later step fails"
        return {
            "name": self.name,
            "description": f"[{self.connector.name}] {description}",
            # What the model reads is what the broker enforces: the operation's schema AND the bounds.
            "inputSchema": narrowed(self.operation.input_schema, [c.schema for c in self.constraints]),
            "annotations": {"readOnlyHint": self.access == "read"},
        }


def is_job_step(run: Run) -> bool:
    """Whether a run is a job step (ADR 0056): the orchestrator wrote `backend: job` and the job's
    spec in its StageInput when it prepared it (`prepare_job`), and the run cannot rewrite either."""
    return run.backend == "job" and isinstance((run.stage_input or {}).get("job"), dict)


async def _selection_of_run(session: AsyncSession, run: Run) -> dict[str, list[str]]:
    """{connector: patterns} that the run's agent version names; nothing without a registry agent."""
    if is_job_step(run):
        job = (run.stage_input or {})["job"]
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


#: What a version grants of a connector: a pattern, or `{name, arguments}` to bind the call (S60-06).
Selection = dict[str, list[Any]]


def _pattern(grant: Any) -> str:
    return str(grant.get("name") or "") if isinstance(grant, dict) else str(grant)


def selection_of_spec(spec: dict[str, Any] | None) -> Selection:
    """{connector: grants} a version's `mcp_servers` names (see `_selection_of_run`): a grant is a
    pattern, or `{name, arguments}` when it binds the call's arguments."""
    selection: Selection = {}
    for server in (spec or {}).get("mcp_servers") or []:
        if isinstance(server, dict) and server.get("connector"):
            selection[str(server["connector"])] = [
                m if isinstance(m, dict) else str(m) for m in server.get("tools") or []
            ]
    return selection


async def broker_tools(session: AsyncSession, run: Run, project: Project) -> dict[str, BrokerTool]:
    return await tools_of_selection(session, await _selection_of_run(session, run), project)


async def tools_of_selection(
    session: AsyncSession, selection: Selection, project: Project
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
    narrowing = {
        p.operation_id: p
        for p in (
            await session.execute(
                select(ProjectOperationPolicy).where(ProjectOperationPolicy.project_id == project.id)
            )
        ).scalars()
    }
    tightened = {operation_id: p.policy for operation_id, p in narrowing.items()}
    enabled = await enabled_ids(session, project)
    tools: dict[str, BrokerTool] = {}
    for operation, connector in rows:
        # ADR 0074: an organisation connector restricted to selected projects reaches only those.
        if connector.availability == SELECTED_PROJECTS and connector.id not in enabled:
            continue
        effective = human_floor(
            connector, operation.name, stricter(operation.policy, tightened.get(operation.id))
        )
        if effective == "forbidden":
            continue
        if operation.groups and not groups & set(operation.groups):
            continue
        grants = [g for g in selection.get(connector.name, []) if fnmatchcase(operation.name, _pattern(g))]
        if not grants:
            continue
        # The grants that bind arguments ALL bind (a plain pattern that also matches does not lift
        # them), then the organisation's rule, then the project's: the call must pass each (S60-06).
        constraints = [
            ArgumentConstraint("agent", interpolate(g["arguments"], project.slug))
            for g in grants
            if isinstance(g, dict) and g.get("arguments")
        ]
        if operation.arguments:
            constraints.append(
                ArgumentConstraint("organisation", interpolate(operation.arguments, project.slug))
            )
        project_rule = narrowing.get(operation.id)
        if project_rule is not None and project_rule.arguments:
            constraints.append(
                ArgumentConstraint("project", interpolate(project_rule.arguments, project.slug))
            )
        name = f"{connector.name}{SEPARATOR}{operation.name}"
        tools[name] = BrokerTool(
            name,
            connector,
            operation,
            needs_approval=effective == "approval",
            constraints=tuple(constraints),
        )
    return tools


async def propose_call(
    session: AsyncSession, run: Run, project: Project, tool: BrokerTool, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A tool under approval, called: a governed action, proposed in the name of the run's agent
    (`agent:<slug>`), or of its job step (`job:<actor>`, `proposer_of`). Nothing reaches the server
    before a re-authenticated human approves it — and whoever owns the agent does not approve it
    (separation of roles)."""
    from ..errors import ApiError

    # ADR 0105, ADR 0149 §7: the route is judged BEFORE the action exists, so a refused connector (mode
    # `required`, `off`, no gateway; an outpost that is paused or revoked) leaves no pending action for a
    # person to approve.
    try:
        await route_of(session, tool.connector)
    except RouteRefusal as refused:
        status, body = tool_error(refused)
        named = {name: value for name, value in body.items() if name not in ("error", "type")}
        raise ApiError(
            status, "Network policy", str(refused), type_="network_policy", extra=named
        ) from refused
    # The same call is the same action (#1379, ADR 0035), as for an allowed write: a model retrying or a
    # script run again (ADR 0122) finds its proposal instead of putting a second one before a person.
    from choregos_contracts import ActionStatus

    from ..db.models import Action

    action_id = call_id(run.id, tool.name, arguments)
    action = await session.get(Action, action_id)
    if action is None:
        action = await _propose_tool_action(session, run, project, tool, arguments, action_id)
    if action.status == ActionStatus.IN_DOUBT.value:
        # The same call is the same action, whatever became of it: one that ended in doubt is said
        # so, and repeating the call proposes nothing new (ADR 0149 §5).
        return _in_doubt(project, action)
    return _pending(project, action)


async def _propose_tool_action(
    session: AsyncSession,
    run: Run,
    project: Project,
    tool: BrokerTool,
    arguments: dict[str, Any],
    action_id: str | None = None,
) -> Any:
    """A tool call's action: a `connector.call` effect, in the name of the run's proposer (`proposer_of`)."""
    proposed_by, justification = proposer_of(run)
    return await tool_action(
        session,
        project,
        tool,
        arguments,
        action_id=action_id,
        proposed_by=proposed_by,
        justification=justification,
        run_id=run.id,
        work_item_id=run.work_item_id,
    )


async def tool_action(  # noqa: PLR0913 - one action: what, by whom, why, keyword-only
    session: AsyncSession,
    project: Project,
    tool: BrokerTool,
    arguments: dict[str, Any],
    *,
    action_id: str | None,
    proposed_by: dict[str, Any],
    justification: str,
    run_id: str | None = None,
    work_item_id: str | None = None,
) -> Any:
    """A governed action of one `connector.call` effect (ADR 0035), proposed by `proposed_by`: a run's
    (`proposer_of`) or an assistant's conversation (S63-10, `conversation_actions`)."""
    from choregos_contracts import ActionOrigin

    from ..rbac import SYSTEM
    from ..schemas.actions import ActionCreate, ActionEffectSpec, ApprovalSpec, Approver
    from .actions import propose

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
    # `{project}-maintainers` is this project's maintainers (S59-11, as for a workflow's people, S59-01):
    # one operation rule of the organisation serves every project, the action stores the resolved group.
    from choregos_core.dsl.groups import group_for

    approval = (
        ApprovalSpec(
            approvers=[
                Approver(role="developer", group=group_for(tool.operation.approver_group, project.slug))
            ]
        )
        if tool.operation.approver_group
        else ApprovalSpec()
    )
    body = ActionCreate(
        kind=f"{tool.connector.name}.{tool.operation.name}",
        title=f"{tool.operation.description or tool.operation.name} ({tool.connector.name})",
        justification=justification,
        params={"arguments": arguments},
        effects=[ActionEffectSpec.model_validate(effect)],
        approval=approval,
        work_item_id=work_item_id,
    )
    return await propose(
        session,
        project,
        body,
        origin=ActionOrigin.TOOL.value,
        proposed_by=proposed_by,
        principal=SYSTEM,
        run_id=run_id,
        action_id=action_id,
    )


def proposer_of(run: Run) -> tuple[dict[str, Any], str]:
    """Who proposes a run's tool action, and the justification that says it (#1364).

    A run of a registry agent proposes as that agent (`agent:<slug>`), whose owner does not decide.
    A job step (ADR 0056) has no registry agent: it proposes as the workflow actor that runs it
    (`job:<actor>`), the name the workflow gives the step. No person owns a job — its command and
    its `tools:` are the workflow's, reviewed with it, like a transition's action (`kind: system`) —
    so the separation of roles excludes nobody (`services/actions.py::_agent_owner`).
    """
    if is_job_step(run):
        name = run.actor or run.transition_id or "job"
        return (
            {"kind": "job", "id": f"job:{name}", "run_id": run.id},
            f"proposed by the job step {name} in run {run.id}",
        )
    return (
        {"kind": "agent", "id": f"agent:{run.agent_slug}", "run_id": run.id},
        f"proposed by the agent {run.agent_slug} in run {run.id}",
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
    # ADR 0105: the route is judged BEFORE the action exists. Refused here, nothing is created, the
    # price is 0 (`created` is False) and the ledger skips a `network_policy` result; judged only in the
    # workers, an action and a priced line would already exist.
    try:
        await route_of(session, tool.connector)
    except RouteRefusal as refused:
        return (*tool_error(refused), False)
    from choregos_contracts import ActionStatus

    from ..config import get_settings
    from ..db.models import Action
    from ..db.session import ALL_ORGS, release_connection, session_scope
    from ..temporal import action_id as temporal_action_id
    from ..temporal import get_temporal
    from .actions import action_policy, approved_by_policy

    action_id = call_id(run.id, tool.name, arguments)
    action = await session.get(Action, action_id)
    created = action is None
    if action is None:
        action = await _propose_tool_action(session, run, project, tool, arguments, action_id)
        worst, _reasons = await action_policy(session, project, action.effects, action.params or {})
        if worst != "allowed":
            # Tightened since the announcement (the organisation, the project): a person decides.
            return 202, _pending(project, action), created
        await approved_by_policy(session, project, action)
        # The row must be VISIBLE before the workflow reads it, and the wait below reads what the
        # worker commits: we commit here, whoever called. The run token's scope only lives for one
        # transaction: the session's next one sets it again, and until then it holds no connection —
        # neither while Temporal is asked nor while the wait below polls (#1354).
        await release_connection(session)
        await get_temporal().start_action(temporal_action_id(action.id), {"action_id": action.id})
    deadline = time.monotonic() + get_settings().courtier_attente_ecriture_s
    in_doubt = ActionStatus.IN_DOUBT.value
    finished = {
        ActionStatus.SUCCEEDED.value,
        ActionStatus.FAILED.value,
        ActionStatus.REJECTED.value,
        in_doubt,
    }
    # A call that found its action already there read it in the request's transaction: that one ends
    # before the wait too.
    async with outside_the_transaction(session):
        while True:
            # One SHORT read per round, on a connection of its own given back at once: a transaction
            # held open between two rounds would keep, under SQLite, the lock that stops the worker
            # from committing what it writes — the wait would bound itself —, and on PostgreSQL a
            # connection idle in transaction.
            async with session_scope(orgs=ALL_ORGS) as reading:
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
    if action.status == in_doubt:
        # Sent, and nobody knows whether it was executed: a claim that got no answer (504), booked
        # like any call that may have reached its server. The agent that repeats the call finds this
        # action again and is told the same: nothing replays it.
        return 504, _in_doubt(project, action), created
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


#: What an agent is told of an action one of whose effects was sent and never answered (ADR 0149 §5),
#: the first time and every time it repeats the call.
IN_DOUBT_MESSAGE = (
    "the write was sent and its answer never came: nobody knows whether it was executed. It is not "
    "replayed and nothing was undone; repeating the call returns this same action. A person checks "
    "the system the write was for"
)


def _in_doubt(project: Project, action: Any) -> dict[str, Any]:
    return {
        "error": action.error or IN_DOUBT_MESSAGE,
        "code": "action_in_doubt",
        "action": action.id,
        "status": action.status,
        "message": IN_DOUBT_MESSAGE,
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


class ArgumentsOutsideGrant(ArgumentsRefused):
    """The arguments satisfy the operation's schema but leave what a level grants (S60-06): answered as
    the problem `arguments_outside_grant`, naming the level and the JSON path."""

    code = "arguments_outside_grant"

    def __init__(self, tool: str, level: str, path: str, message: str) -> None:
        super().__init__(f"arguments outside the {level} grant of {tool} at {path}: {message}")
        self.level = level
        self.path = path


def check_arguments(tool: BrokerTool, arguments: dict[str, Any]) -> None:
    """The announced schema is applied BEFORE the call: a paying provider does not receive a
    malformed call, and the registry does not count it. Then each level's constraint (S60-06): a call
    the schema accepts may still leave what the agent, the organisation or the project grants."""
    if tool.operation.input_schema:
        import jsonschema

        try:
            jsonschema.validate(arguments, tool.operation.input_schema)
        except jsonschema.ValidationError as exc:
            path = "/".join(str(p) for p in exc.absolute_path) or "(root)"
            raise ArgumentsRefused(
                f"argument refused by the schema of {tool.name} at {path}: {exc.message}"
            ) from exc
    for constraint in tool.constraints:
        found = violation(constraint.schema, arguments)
        if found is not None:
            raise ArgumentsOutsideGrant(tool.name, constraint.level, *found)


async def call(
    session: AsyncSession,
    tool: BrokerTool,
    arguments: dict[str, Any],
    *,
    run: Run | None = None,
    project: Project | None = None,
    acting: Acting | None = None,
) -> tuple[int, dict[str, Any]]:
    """`tools/call` on the server, with the connector's key resolved HERE, never in the pod.

    `acting` (ADR 0135): `None` or `service` is the connector's own identity, as ever; `user_exchange`
    calls it with a token exchanged for the person, and fails with `identity_unavailable` (422, no
    call made) when that token cannot be had — never under the connector's identity.

    Two halves of one network policy hold it (ADR 0070, amended): the organisation's network bounds
    are read FIRST, so a host its ceiling refuses (403) or that the organisation already called too
    often this minute (429) never receives the call; then every request runs under the
    organisation's egress allowlist (S39-22, ADR 0066), so a host it has not opened is a tool error
    that says so, not a timeout."""
    from .egress import egress_scope_for_org

    async with egress_scope_for_org(session, tool.connector.org_id, "api", f"mcp:{tool.connector.name}"):
        return await _call(session, tool, arguments, run=run, project=project, acting=acting)


async def _client_for(
    session: AsyncSession,
    tool: BrokerTool,
    via_gateway: bool,
    run: Run | None,
    project: Project | None,
    acting: Acting | None,
) -> Any:
    """The client of one call. A person's mode (ADR 0135) never goes through a gateway and never falls back
    to the connector's own identity: its token is one call's, exchanged or drawn here and handed to this
    client only."""
    instance = tool.connector
    mode = acting.mode if acting is not None else None
    if acting is not None and mode in (USER_EXCHANGE, USER_CONSENT):
        if via_gateway:
            raise IdentityUnavailable(f"the `{mode}` identity does not go through an MCP gateway")
        from .plugin_servers import connector_client as direct_client

        if mode == USER_EXCHANGE:
            return await direct_client(
                session, instance, bearer=await exchanged_token(session, instance, acting)
            )
        token = await consented_token(session, instance, acting.user_id, acting.org_id)
        return await direct_client(session, instance, user_token=token)
    return await connector_client(
        session, instance, lambda: identity_of_run(session, run, project, instance, tool.operation.name)
    )


async def _call(
    session: AsyncSession,
    tool: BrokerTool,
    arguments: dict[str, Any],
    *,
    run: Run | None = None,
    project: Project | None = None,
    acting: Acting | None = None,
) -> tuple[int, dict[str, Any]]:
    import httpx
    from choregos_adapters.errors import AdapterError
    from choregos_adapters.mcp import McpError
    from choregos_core.redaction import redact
    from choregos_core.secrets import SecretNotFound

    instance = tool.connector
    # ADR 0105, ADR 0149: how the connector is reached is decided first; a refusal of the route (the
    # gateway's, an outpost's) leaves nothing behind.
    try:
        route = await route_of(session, instance)
    except RouteRefusal as refused:
        return tool_error(refused)
    try:
        await enforce_connector_call(
            session,
            instance,
            project=project,
            run_id=run.id if run else None,
            agent_slug=run.agent_slug if run else None,
            # The gateway's host is judged like any other; its politeness is the gateway's job (ADR 0105).
            polite=not route.via_gateway,
            host=route.host,
        )
    except PlatformNetworkRefused as refused:
        return 403, {"error": f"{refused.message()}: {WHERE}", "type": "network_policy"}
    except PlatformRateLimited as rate_limited:
        return 429, {"error": str(rate_limited), "type": "rate_limited"}
    try:
        # A gateway connector (ADR 0105) goes through the gateway, signed with this call's identity; a
        # connector at an outpost (ADR 0149), through that outpost's rendezvous; a plugin's server
        # (ADR 0096), its Service and bearer token; any other, its type's adapter.
        client = await _client_for(session, tool, route.via_gateway, run, project, acting)
        # A server may take thirty seconds to answer: the caller's transaction, when it allows it,
        # does not wait for it on a pooled connection (#1354).
        async with outside_the_transaction(session):
            if hasattr(client, "call_tool"):
                result = await client.call_tool(tool.operation.name, arguments)
            else:
                # A connector that is not MCP (a directory): its declared operation, by its name,
                # returned in the format of an MCP result — the agent reads the same everywhere.
                raw = await client.execute(tool.operation.name, arguments)
                result = {"content": [{"type": "text", "text": _json(raw)}], "structuredContent": raw}
    except AccountNotConnected as missing:
        return 422, {"error": str(missing), "type": NOT_CONNECTED, "connect_url": missing.connect_url}
    except IdentityUnavailable as unavailable:
        return 422, {"error": str(unavailable), "type": UNAVAILABLE}
    except McpError as mcp_error:
        return 502, {"error": redact(str(mcp_error))}
    except RouteRefusal as refused:
        # A lost assertion key or credential of the gateway; an outpost that is offline, that refused,
        # that did not answer in time, or a call its door did not admit: refused, never a 500.
        return tool_error(refused)
    except AdapterError as adapter_refusal:
        return 422, {"error": redact(str(adapter_refusal))}
    except (SecretNotFound, httpx.HTTPError) as failure:
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
    than `result_limit(declared)`, its data counted once (#1374): a text block that serializes its
    `structuredContent` — how `_call` wraps a directory's answer, what MCP asks a server to write —
    is not counted again, since an agent reads one or the other (the run's tool server shows the
    model the structure, a script gets the structure); a text that says something else
    (`web_fetch`'s page, a server's summary) is counted with it. A larger one keeps its text, cut,
    with `TRUNCATED` at the end. Its `structuredContent` is dropped when a text block serializes it:
    cutting the text cuts it, and half an object is not the object. When the text says something
    else, the structure is another datum, whole: it stays beside the cut text when both fit the bound
    (`web_fetch`'s page cut, what was fetched kept, #1383). `cut=False` masks only: a job reads the
    answer, not a model (S40-13b)."""
    masked = _masked(result)
    if not cut:
        return masked
    limit = result_limit(declared)
    total = len(_json(data_once(masked)).encode("utf-8"))
    if total <= limit:
        return masked
    kept = {k: v for k, v in masked.items() if k not in ("content", "structuredContent")}
    text = result_text(masked)
    structured = masked.get("structuredContent")
    if structured is not None and not _serialized_in_text(masked, structured):
        beside = _cut(
            text,
            lambda note: {
                **kept,
                "content": [{"type": "text", "text": note}],
                "structuredContent": structured,
            },
            total,
            limit,
        )
        if len(_json(beside).encode("utf-8")) <= limit:
            return beside
    return _cut(text, lambda note: {**kept, "content": [{"type": "text", "text": note}]}, total, limit)


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
    return any(serializes(block, structured) for block in result.get("content") or [])


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
