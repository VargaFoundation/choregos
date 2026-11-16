# SPDX-License-Identifier: Apache-2.0
"""Updating an agent and moving it through its life: lifecycle, deletion, transfer, fork and scope."""

from __future__ import annotations

from choregos_core import utcnow
from fastapi import Response, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import record
from ...db.models import (
    Agent,
    AgentCredential,
    AgentVersion,
    Membership,
    Project,
    ProjectAgent,
    User,
    WorkflowDef,
)
from ...deps import Db, Me
from ...errors import conflict, not_found, unprocessable
from ...schemas import (
    AgentDto,
    AgentFork,
    AgentLifecycleChange,
    AgentLifecycleResult,
    AgentNotStopped,
    AgentOwnerChange,
    AgentPatch,
    AgentScopeChange,
    AgentSpec,
    AgentStopResult,
    StopRequest,
)
from ...services.agent_access import (
    enforce_decision,
    load_visible_agent,
    project_in_org,
)
from ...services.agent_governance import (
    Action,
    decide,
)
from ...services.agent_lifecycle import (
    TransitionRefused,
    check_transition,
    delete_blockers,
    retire,
    running_work_items,
)
from ...services.agent_releases import (
    after_publish,
)
from ...services.agent_social import auto_watch, notify
from ...services.agent_stop import stop_agent as stop_the_agent
from ...services.agent_views import agent_dto, agent_versions
from ...services.memory_access import check_bindings_at_publish
from ._router import Slug, router


@router.patch("/orgs/{org}/agents/{slug}", response_model=AgentDto, operation_id="updateAgent")
async def update_agent(org: str, slug: Slug, body: AgentPatch, session: Db, principal: Me) -> AgentDto:
    """Its name, description, expiry, state. `revoked` is final."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.PUBLISH))
    if body.status is not None:
        enforce_decision(decide(principal, org, subject, Action.MANAGE))
    if agent.status == "revoked" and body.status not in {None, "revoked"}:
        raise conflict(f"agent `{slug}` is revoked: revocation is final")
    for field_name in ("display_name", "description", "expires_at"):
        if field_name in body.model_fields_set:
            setattr(agent, field_name, getattr(body, field_name))
    if body.tags is not None:
        agent.tags = list(body.tags)
    if body.status is not None and body.status != agent.status:
        agent.status = body.status
        if body.status == "revoked":
            agent.revoked_at = utcnow()
    await record(
        session,
        principal,
        "agent.update",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        status=agent.status,
        tags=agent.tags,
    )
    return await agent_dto(session, agent, principal, org)


# ───────────────────────────── its life (S32-04) ─────────────────────────────


@router.post(
    "/orgs/{org}/agents/{slug}/lifecycle",
    response_model=AgentLifecycleResult,
    operation_id="changeAgentLifecycle",
)
async def change_agent_lifecycle(
    org: str, slug: Slug, body: AgentLifecycleChange, session: Db, principal: Me
) -> AgentLifecycleResult:
    """Activate a draft, deprecate, reactivate, or retire. Retiring runs its checklist — credentials
    revoked, project pins removed, new runs refused — and is refused while a run of the agent is in
    flight, unless `force` (audited)."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.PUBLISH))
    avant = agent.lifecycle or "active"
    try:
        check_transition(avant, body.to)
    except TransitionRefused as refus:
        raise conflict(str(refus)) from refus
    lifecycle_result = AgentLifecycleResult(
        agent=await agent_dto(session, agent, principal, org), forced=body.force
    )
    if body.to == "retired":
        in_flight = await running_work_items(session, organisation.id, slug)
        if in_flight and not body.force:
            raise conflict(
                f"agent `{slug}` has runs in flight on {', '.join(in_flight)}: let them finish, or force "
                "the retirement"
            )
        retirement = await retire(session, agent)
        lifecycle_result.revoked_credentials = retirement["revoked_credentials"]
        lifecycle_result.unpinned_projects = retirement["unpinned_projects"]
        lifecycle_result.running_work_items = in_flight
    else:
        agent.lifecycle = body.to
        await session.flush()
    await record(
        session,
        principal,
        f"agent.{'retire' if body.to == 'retired' else 'lifecycle'}",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        before=avant,
        after=agent.lifecycle,
        reason=body.reason,
        forced=body.force if body.to == "retired" else None,
        revoked_credentials=lifecycle_result.revoked_credentials,
        unpinned_projects=lifecycle_result.unpinned_projects,
        running_work_items=lifecycle_result.running_work_items,
    )
    if body.to in {"deprecated", "retired"}:
        await notify(session, agent, "lifecycle_changed", principal, to=body.to, before=avant)
    lifecycle_result.agent = await agent_dto(session, agent, principal, org)
    return lifecycle_result


@router.delete(
    "/orgs/{org}/agents/{slug}", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteAgent"
)
async def delete_agent(org: str, slug: Slug, session: Db, principal: Me) -> Response:
    """Delete an agent that never served: no project pin, no run outside its trials, named by no
    active workflow. Anything else is refused (409) with its reasons — retire it instead, which keeps
    its name in the history. Its versions, credentials, evaluations and releases go with it."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.MANAGE))
    if reasons := await delete_blockers(session, organisation.id, agent):
        raise conflict(f"agent `{slug}` cannot be deleted: {'; '.join(reasons)}. Retire it instead")
    await record(
        session,
        principal,
        "agent.delete",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        slug=slug,
        lifecycle=agent.lifecycle,
        scope=agent.scope,
    )
    await session.execute(sa_delete(Agent).where(Agent.id == agent.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/orgs/{org}/agents/{slug}/owner", response_model=AgentDto, operation_id="transferAgentOwnership"
)
async def transfer_agent_ownership(
    org: str, slug: Slug, body: AgentOwnerChange, session: Db, principal: Me
) -> AgentDto:
    """A new owner, a member of the organisation. The agent keeps its credentials: they belong to
    the agent, not to the person who owned it."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.MANAGE))
    if agent.lifecycle == "retired":
        raise conflict(f"agent `{slug}` is retired: it has no owner to change")
    new_owner = (
        await session.execute(select(User).where(func.lower(User.email) == body.email.strip().lower()))
    ).scalar_one_or_none()
    is_member = (
        new_owner is not None
        and (
            await session.execute(
                select(Membership.id).where(
                    Membership.user_id == new_owner.id, Membership.org_id == organisation.id
                )
            )
        ).first()
        is not None
    )
    if new_owner is None or not is_member:
        raise unprocessable(f"`{body.email}` is not a member of {org}: the owner must be one")
    previous_owner = await session.get(User, agent.owner_id) if agent.owner_id else None
    agent.owner_id = new_owner.id
    await session.flush()
    await auto_watch(session, agent, new_owner.id)
    credential_count = (
        await session.execute(
            select(func.count(AgentCredential.id)).where(
                AgentCredential.agent_id == agent.id, AgentCredential.revoked_at.is_(None)
            )
        )
    ).scalar_one()
    await record(
        session,
        principal,
        "agent.transfer",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        before=previous_owner.email if previous_owner else None,
        after=new_owner.email,
        credentials_kept=int(credential_count),
    )
    return await agent_dto(session, agent, principal, org)


# ───────────────────────────── forks and promotion (ADR 0061) ─────────────────────────────


@router.post(
    "/orgs/{org}/agents/{slug}/fork",
    response_model=AgentDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="forkAgent",
)
async def fork_agent(org: str, slug: Slug, body: AgentFork, session: Db, principal: Me) -> AgentDto:
    """A new agent, in the scope asked, whose version 1 is a byte-for-byte copy of one of the source's
    versions (its latest by default) — same spec, same checksum. It records where it came from and
    inherits nothing else: no pin, no client, no metrics. The source is never touched."""
    organisation, source, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(
        decide(principal, org, subject, Action.FORK, project_slug=body.project, target_scope=body.scope)
    )
    project_row = await project_in_org(session, organisation, body.project) if body.project else None
    versions = await agent_versions(session, source)
    if body.version is None:
        copiee = versions[0] if versions else None
    else:
        copiee = next((v for v in versions if v.version == body.version), None)
    if copiee is None:
        raise not_found("Agent version", f"{slug}@{body.version or 'latest'}")
    # The copy binds the same memories: the forker must see the global ones (S40-11).
    await check_bindings_at_publish(
        session,
        principal,
        organisation,
        AgentSpec.model_validate(copiee.spec),
        project_slug=body.project if body.scope == "project" else None,
    )
    if (
        await session.execute(
            select(Agent.id).where(Agent.org_id == organisation.id, Agent.slug == body.slug)
        )
    ).first() is not None:
        raise conflict(f"agent `{body.slug}` already exists in {org}")
    fork = Agent(
        org_id=organisation.id,
        slug=body.slug,
        kind=source.kind,
        display_name=body.display_name or f"{source.display_name} (fork)",
        description=source.description,
        tags=list(source.tags or []),
        owner_id=principal.user_id,
        status="active",
        scope=body.scope,
        project_id=project_row.id if project_row else None,
        forked_from_agent_id=source.id,
        forked_from_slug=source.slug,
        forked_from_version=copiee.version,
    )
    session.add(fork)
    await session.flush()
    session.add(
        AgentVersion(
            agent_id=fork.id,
            org_id=organisation.id,
            version=1,
            spec=copiee.spec,
            checksum=copiee.checksum,
            created_by=principal.email,
            message=(body.message or "").strip() or f"Forked from {source.slug}@{copiee.version}",
        )
    )
    await session.flush()
    await after_publish(session, principal, fork, 1, principal.email)
    if principal.kind == "user":
        await auto_watch(session, fork)
    await notify(session, source, "forked", principal, related=fork, version=copiee.version)
    await record(
        session,
        principal,
        "agent.fork",
        org_id=organisation.id,
        target_type="agent",
        target_id=fork.id,
        source=source.slug,
        source_version=copiee.version,
        scope=fork.scope,
        project=body.project,
    )
    return await agent_dto(session, fork, principal, org, with_versions=True)


async def _named_outside(session: AsyncSession, agent: Agent, project: Project) -> list[str]:
    """The other projects of the organisation that pin the agent or name it in an active workflow."""
    outside: set[str] = set()
    epingles = await session.execute(
        select(Project.slug)
        .join(ProjectAgent, ProjectAgent.project_id == Project.id)
        .where(ProjectAgent.agent_id == agent.id, Project.id != project.id)
    )
    outside.update(epingles.scalars())
    workflows = await session.execute(
        select(Project.slug, WorkflowDef.json_doc)
        .join(Project, Project.id == WorkflowDef.project_id)
        .where(Project.org_id == agent.org_id, Project.id != project.id, WorkflowDef.is_active.is_(True))
    )
    for other_project_slug, document in workflows.all():
        for actor in ((document or {}).get("actors") or {}).values():
            named = str(actor.get("agent") or "") if isinstance(actor, dict) else ""
            if named.split("@")[0] == agent.slug:
                outside.add(other_project_slug)
    return sorted(outside)


@router.post("/orgs/{org}/agents/{slug}/scope", response_model=AgentDto, operation_id="changeAgentScope")
async def change_agent_scope(
    org: str, slug: Slug, body: AgentScopeChange, session: Db, principal: Me
) -> AgentDto:
    """Promote an agent (ADR 0061): a personal agent to a project (its owner AND that project's owner,
    or an org_admin) or to the organisation (an org_admin); a project's agent to the organisation; an
    organisation agent into one project (an org_admin; refused while another project pins or names
    it). A common agent never moves."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(
        decide(principal, org, subject, Action.MOVE, project_slug=body.project, target_scope=body.scope)
    )
    project_row = await project_in_org(session, organisation, body.project) if body.project else None
    if project_row is not None:
        outside = await _named_outside(session, agent, project_row)
        if outside:
            raise conflict(
                f"`{slug}` is pinned or named by {', '.join(outside)}: it would stop running there. Fork it "
                "into those projects, or unpin it, first"
            )
    avant = agent.scope
    agent.scope = body.scope
    agent.project_id = project_row.id if project_row else None
    await session.flush()
    await record(
        session,
        principal,
        "agent.scope",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        before=avant,
        after=agent.scope,
        project=body.project,
    )
    return await agent_dto(session, agent, principal, org)


# ───────────────────────────── a stop (S64-04, ADR 0142 §4) ─────────────────────────────


@router.post("/orgs/{org}/agents/{slug}/stop", response_model=AgentStopResult, operation_id="stopAgent")
async def stop_agent(org: str, slug: Slug, body: StopRequest, session: Db, principal: Me) -> AgentStopResult:
    """Suspend the agent, then stop its runs in flight in every project and close its open sessions
    (ADR 0142 §4). The right is the one that guards a status change: `Action.MANAGE` on the agent.
    A revoked agent is final (409). Stopping a suspended agent stops what is still in flight."""
    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.MANAGE))
    if agent.status == "revoked":
        raise conflict(f"agent `{slug}` is revoked: there is nothing to stop, revocation is final")
    stop = await stop_the_agent(session, principal, organisation, agent, body.reason)
    return AgentStopResult(
        slug=agent.slug,
        runs_cancelled=len(stop.runs_cancelled),
        sessions_closed=len(stop.sessions_closed),
        turns_cancelled=len(stop.turns_cancelled),
        not_stopped=[AgentNotStopped(kind=n.kind, id=n.id, detail=n.detail) for n in stop.not_stopped],
    )
