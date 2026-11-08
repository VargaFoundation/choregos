# SPDX-License-Identifier: Apache-2.0
"""A project's pins of agents and the agents its workflows use."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Path, Query, Response, status
from pydantic import StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import record
from ...db.models import (
    Agent,
    AgentVersion,
    Organization,
    ProjectAgent,
    WorkflowDef,
)
from ...deps import Config, Db, ProjectCtx
from ...errors import conflict, not_found, unprocessable
from ...rbac import Permission
from ...schemas import (
    AgentOverrides,
    AgentResolution,
    AgentSpec,
    BuiltinAgent,
    BuiltinMcpServer,
    BuiltinModelChoice,
    GroupMember,
    GroupMembers,
    ImplicitAgent,
    ProjectAgentDto,
    ProjectAgentPut,
    WorkflowAgent,
)
from ...services.agent_access import (
    agent_in_org,
    enforce_decision,
)
from ...services.agent_governance import (
    Action,
    decide,
    subject_of,
)
from ...services.agent_releases import (
    served,
)
from ...services.agents import (
    AgentUnavailable,
    effective,
    resolve_agent,
    widenings,
)
from ._router import AgentRef, Slug, router


async def _version_row(session: AsyncSession, agent: Agent, version: int) -> AgentVersion:
    db_row = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if db_row is None:
        raise not_found("Agent version", f"{agent.slug}@{version}")
    return db_row


async def _latest(session: AsyncSession, agent: Agent) -> int:
    return (
        await session.execute(select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id))
    ).scalar() or 1


async def _served_version(session: AsyncSession, agent: Agent, pin_row: ProjectAgent | None) -> int:
    """The revision a pin serves now: its fixed one, or the one of the channel it follows."""
    version, _, _ = served(agent, pin=pin_row, project_channel="stable", work_item_id=None)
    return version if version is not None else await _latest(session, agent)


async def _project_agent_dto(session: AsyncSession, pin_row: ProjectAgent, agent: Agent) -> ProjectAgentDto:
    version = await _served_version(session, agent, pin_row)
    spec = AgentSpec.model_validate((await _version_row(session, agent, version)).spec)
    overrides_model = AgentOverrides.model_validate(pin_row.overrides or {})
    return ProjectAgentDto(
        agent=agent.slug,
        version=version,
        channel=pin_row.channel,
        overrides=overrides_model,
        effective=effective(spec, overrides_model),
        warning=(
            f"agent `{agent.slug}` is deprecated: it still runs, but plan to replace it"
            if agent.lifecycle == "deprecated"
            else None
        ),
    )


@router.get("/projects/{id}/agents", response_model=list[ProjectAgentDto], operation_id="listProjectAgents")
async def list_project_agents(ctx: ProjectCtx, session: Db) -> list[ProjectAgentDto]:
    db_rows = await session.execute(
        select(ProjectAgent, Agent)
        .join(Agent, Agent.id == ProjectAgent.agent_id)
        .where(ProjectAgent.project_id == ctx.project.id)
        .order_by(Agent.slug)
    )
    return [await _project_agent_dto(session, pin_row, agent) for pin_row, agent in db_rows.tuples()]


async def _agent_actors(ctx: ProjectCtx, session: Db) -> list[WorkflowAgent]:
    """Every `agent` actor of the project's active workflows, with the registry agent it names, if any,
    and whether that agent exists in the organisation."""
    registry = set(
        (await session.execute(select(Agent.slug).where(Agent.org_id == ctx.project.org_id))).scalars()
    )
    workflows = (
        await session.execute(
            select(WorkflowDef.name, WorkflowDef.json_doc)
            .where(WorkflowDef.project_id == ctx.project.id, WorkflowDef.is_active.is_(True))
            .order_by(WorkflowDef.name)
        )
    ).all()
    found: list[WorkflowAgent] = []
    for name, document in workflows:
        actors = (document or {}).get("actors") or {}
        for actor, definition in actors.items():
            if not isinstance(definition, dict) or definition.get("type") != "agent":
                continue
            named = str(definition.get("agent") or "") or None
            found.append(
                WorkflowAgent(
                    workflow=name,
                    actor=actor,
                    role=definition.get("role"),
                    model=definition.get("model"),
                    backend=definition.get("backend"),
                    max_turns=definition.get("max_turns"),
                    max_minutes=definition.get("max_minutes"),
                    agent=named,
                    registered=bool(named) and str(named).split("@")[0] in registry,
                )
            )
    return found


@router.get(
    "/projects/{id}/implicit-agents", response_model=list[ImplicitAgent], operation_id="listImplicitAgents"
)
async def list_implicit_agents(ctx: ProjectCtx, session: Db) -> list[ImplicitAgent]:
    """The `agent` actors of the project's active workflows that name no agent of the registry
    (S30-04): the console used to read every workflow, one request each, to find them."""
    return [
        ImplicitAgent(
            workflow=a.workflow,
            actor=a.actor,
            role=a.role,
            model=a.model,
            backend=a.backend,
            max_turns=a.max_turns,
            max_minutes=a.max_minutes,
        )
        for a in await _agent_actors(ctx, session)
        if not a.registered
    ]


@router.get(
    "/projects/{id}/workflow-agents", response_model=list[WorkflowAgent], operation_id="listWorkflowAgents"
)
async def list_workflow_agents(ctx: ProjectCtx, session: Db) -> list[WorkflowAgent]:
    """Every agent the project's active workflows run (S47-39): the registry agents they name and the
    built-in ones alike, so the console lists them in one table."""
    return await _agent_actors(ctx, session)


#: A role or playbook name, as the workflow DSL takes it; also what keeps a name from leaving the
#: playbook directories.
PlaybookName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
#: The sizes `profile:by_size` tells apart (`choregos_core.models.BY_SIZE_MAP`).
_SIZES = ("S", "M", "L", "XL")


@router.get("/projects/{id}/builtin-agent", response_model=BuiltinAgent, operation_id="describeBuiltinAgent")
async def describe_builtin_agent(  # noqa: PLR0913 - the actor's own fields, as query parameters
    ctx: ProjectCtx,
    session: Db,
    settings: Config,
    role: Annotated[PlaybookName, Query()],
    playbook: Annotated[PlaybookName | None, Query()] = None,
    model: Annotated[str, Query(min_length=1, max_length=200)] = "profile:by_size",
    backend: Annotated[str | None, Query(max_length=64)] = None,
    max_turns: Annotated[int | None, Query(ge=1, le=1000)] = None,
    max_minutes: Annotated[int | None, Query(ge=1, le=480)] = None,
) -> BuiltinAgent:
    """What an `agent` actor that names no registry agent runs in this project: the role's playbook
    (the prompt, as a run receives it), the model each work item size resolves to, the backend, the
    limits, the memory and the MCP servers. The console calls it with a step's own fields, so a
    draft is described too. Read only, no audit record."""
    from choregos_core import ModelResolutionError, ModelResolver
    from choregos_core.models import BY_SIZE
    from choregos_playbooks import playbook_instructions, playbook_origin, preview_playbook
    from choregos_tools_mcp.tools import TOOL_SCHEMAS

    from ...catalogue import project_tools
    from ...db.models import ModelProfileRow
    from ...services.definitions import policy_engine, project_config
    from ...services.model_catalogue import load_policy

    config = project_config(ctx.project)
    name = playbook or role
    instructions: str | None = None
    template: str | None = None
    origin: str | None = None
    error: str | None = None
    try:
        instructions = preview_playbook(name)
        template = playbook_instructions(name)
        origin = playbook_origin(name)
    except FileNotFoundError as exc:
        error = str(exc)

    chosen = backend or config.agent.default_backend
    rows = (
        (await session.execute(select(ModelProfileRow).where(ModelProfileRow.scope == "platform")))
        .scalars()
        .all()
    )
    resolver = ModelResolver(
        gateway_url="",
        gateway_models=await _gateway_catalogue(),
        validated_backends={row.litellm_model: list(row.validated_backends or []) for row in rows},
        catalogue=await load_policy(session, ctx.project.org_id),
    )
    choices: list[BuiltinModelChoice] = []
    for size in _SIZES if model == BY_SIZE else (None,):
        try:
            resolved = resolver.resolve(model, project=config, size=size, backend=chosen, role=role)
        except ModelResolutionError as exc:
            choices.append(BuiltinModelChoice(size=size, error=str(exc)))
            continue
        choices.append(
            BuiltinModelChoice(
                size=size,
                profile=resolved.profile,
                litellm_model=resolved.litellm_model,
                provider_model=resolved.provider_model,
                turns_factor=resolved.turns_factor,
                warnings=list(resolved.warnings),
            )
        )

    engine = await policy_engine(session, ctx.project.id)
    servers = [
        BuiltinMcpServer(
            name="choregos",
            description="The platform's tools; every write goes through the internal API",
            tools=[str(tool["name"]) for tool in TOOL_SCHEMAS]
            + [tool.name for tool in project_tools(list(config.tools or []), list(config.groups or []))],
        )
    ]
    # A playbook run binds the project's shared memory, written (`_memory_bindings` in the stage).
    if settings.memory_url:
        servers.append(BuiltinMcpServer(name="memory", description="The project's shared memory (Ecphoria)"))
    return BuiltinAgent(
        role=role,
        playbook=name,
        playbook_origin=origin,
        instructions=instructions,
        instructions_error=error,
        instructions_template=template,
        model=model,
        models=choices,
        backend=chosen,
        backend_source="actor" if backend else "project",
        max_turns=max_turns or engine.max_turns(role),
        max_minutes=max_minutes or engine.max_minutes(role),
        limits_source="actor" if max_turns or max_minutes else "policy",
        memory=["project:shared (write)"],
        mcp_servers=servers,
    )


@router.get(
    "/projects/{id}/groups/{group}/members", response_model=GroupMembers, operation_id="listGroupMembers"
)
async def list_group_members(
    ctx: ProjectCtx,
    session: Db,
    group: Annotated[str, Path(min_length=1, max_length=200)],
) -> GroupMembers:
    """Who a person step addressed to `group` waits for: the organisation's members carrying that
    identity-provider group, and whether each may answer in this project. Empty means the step waits
    for an organisation administrator. Needs `member:read` on the project, like its members list."""
    from choregos_core.dsl.groups import group_for

    from ...services.human_groups import members_of_group

    ctx.require(Permission.MEMBER_READ)
    # A per-project group (`{project}-maintainers`, S59-01) is this project's.
    group = group_for(group, ctx.project.slug)
    members = await members_of_group(session, ctx.project.org_id, ctx.org_slug, ctx.project.id, group)
    return GroupMembers(
        group=group,
        members=[
            GroupMember(email=user.email, display_name=user.display_name or "", can_decide=decides)
            for user, decides in members
        ],
    )


async def _gateway_catalogue() -> list[Any]:
    """The models the deployment's gateway declares, so an alias shows the model behind it; a gateway
    that cannot list them leaves the alias as it is (the stage does the same)."""
    from choregos_adapters import build
    from choregos_adapters.registry import default_gateway_type

    try:
        return list(await build("gateway", default_gateway_type(), {}).list_models())
    except Exception:  # the catalogue sharpens the answer, it is not a precondition
        return []


@router.get(
    "/projects/{id}/agent-resolution",
    response_model=list[AgentResolution],
    operation_id="resolveProjectAgents",
)
async def resolve_project_agents(
    ctx: ProjectCtx, session: Db, ref: Annotated[list[AgentRef], Query(min_length=1, max_length=64)]
) -> list[AgentResolution]:
    """Which agent version a run of this project would load for each reference (S39-13).

    It delegates to the runtime's own resolver, so the console shows exactly what a run loads: an
    explicit `@N`, else the project's pin, else the project's channel, else production (ADR 0071).
    A canary share depends on the work item, so it is reported (`candidate_percent`), not applied.
    Read only, no audit record."""
    from ...services.agent_catalogue import catalogue_of

    offered = await catalogue_of(session, ctx.project.org_id)
    reponses: list[AgentResolution] = []
    for reference in ref:
        slug = reference.partition("@")[0]
        try:
            resolu = await resolve_agent(session, ctx.project.org_id, ctx.project.id, reference)
        except AgentUnavailable as erreur:
            agent = (
                await session.execute(
                    select(Agent).where(Agent.org_id == ctx.project.org_id, Agent.slug == slug)
                )
            ).scalar_one_or_none()
            if agent is None:
                reponses.append(
                    AgentResolution(
                        ref=reference,
                        slug=slug,
                        status="not_installed",
                        in_catalogue=offered.entry(slug) is not None,
                        reason=str(erreur),
                    )
                )
            else:
                reponses.append(
                    AgentResolution(
                        ref=reference,
                        slug=slug,
                        status="unavailable",
                        display_name=agent.display_name,
                        lifecycle=agent.lifecycle,
                        reason=str(erreur),
                    )
                )
            continue
        reponses.append(
            AgentResolution(
                ref=reference,
                slug=slug,
                status="resolved",
                version=resolu.version,
                pinned=resolu.pinned,
                source=resolu.source,
                channel=resolu.channel,
                candidate_version=resolu.agent.candidate_version,
                candidate_percent=(
                    resolu.agent.candidate_percent if resolu.agent.candidate_version is not None else None
                ),
                display_name=resolu.agent.display_name,
                lifecycle=resolu.agent.lifecycle,
            )
        )
    return reponses


@router.put("/projects/{id}/agents/{slug}", response_model=ProjectAgentDto, operation_id="pinProjectAgent")
async def pin_agent(slug: Slug, body: ProjectAgentPut, ctx: ProjectCtx, session: Db) -> ProjectAgentDto:
    """Pin a version of the agent on the project; an override that widens gets a 422."""
    ctx.require(Permission.PROJECT_WRITE)
    organisation = await session.get(Organization, ctx.project.org_id)
    assert organisation is not None
    agent = await agent_in_org(session, organisation, slug)
    subject = await subject_of(session, agent)
    if not decide(ctx.principal, organisation.slug, subject, Action.SEE).allowed:
        raise not_found("Agent", f"{organisation.slug}/{slug}")
    enforce_decision(
        decide(ctx.principal, organisation.slug, subject, Action.PIN, project_slug=ctx.project.slug)
    )
    if agent.status != "active":
        raise conflict(f"agent `{slug}` is {agent.status}: it cannot be pinned")
    if agent.lifecycle in {"draft", "retired"}:
        raise conflict(f"agent `{slug}` is {agent.lifecycle}: it cannot be pinned")
    if body.version is not None:
        pinned_version = body.version
    elif body.channel == "candidate" and agent.candidate_version is not None:
        pinned_version = agent.candidate_version
    else:
        pinned_version = agent.stable_version or await _latest(session, agent)
    spec = AgentSpec.model_validate((await _version_row(session, agent, pinned_version)).spec)
    ecarts = widenings(spec, body.overrides)
    if ecarts:
        raise unprocessable(
            "a project can only narrow an agent version, never widen it",
            [{"loc": ["overrides"], "msg": ecart} for ecart in ecarts],
        )
    pin_row = (
        await session.execute(
            select(ProjectAgent).where(
                ProjectAgent.project_id == ctx.project.id, ProjectAgent.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    if pin_row is None:
        pin_row = ProjectAgent(
            project_id=ctx.project.id, org_id=organisation.id, agent_id=agent.id, version=1
        )
        session.add(pin_row)
    pin_row.version = body.version
    pin_row.channel = body.channel
    pin_row.overrides = body.overrides.model_dump(mode="json")
    await session.flush()
    await record(
        session,
        ctx.principal,
        "agent.pin",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        project=ctx.project.slug,
        version=body.version,
        channel=body.channel,
    )
    return await _project_agent_dto(session, pin_row, agent)


@router.delete(
    "/projects/{id}/agents/{slug}", status_code=status.HTTP_204_NO_CONTENT, operation_id="unpinProjectAgent"
)
async def unpin_agent(slug: Slug, ctx: ProjectCtx, session: Db) -> Response:
    ctx.require(Permission.PROJECT_WRITE)
    organisation = await session.get(Organization, ctx.project.org_id)
    assert organisation is not None
    agent = await agent_in_org(session, organisation, slug)
    pin_row = (
        await session.execute(
            select(ProjectAgent).where(
                ProjectAgent.project_id == ctx.project.id, ProjectAgent.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    if pin_row is None:
        raise not_found("Pin", f"{slug} on {ctx.project.slug}")
    await session.delete(pin_row)
    await record(
        session,
        ctx.principal,
        "agent.unpin",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        project=ctx.project.slug,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
