# SPDX-License-Identifier: Apache-2.0
"""Listing, creating, drafting and reading agents, and the hosted sources they are built from."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Query, Request, status
from sqlalchemy import func, or_, select
from tranibor_adapters.storage import ObjectStoreError

from ...audit import record
from ...db.json_lists import has_tag
from ...db.models import (
    Agent,
    AgentRelease,
    AgentStar,
    AgentVersion,
)
from ...deps import Db, Me, Pagination
from ...errors import ApiError, conflict, forbidden, not_found, unprocessable, upstream
from ...keyset import keyset_page
from ...object_store import object_store
from ...rbac import Permission
from ...schemas import (
    AgentCreate,
    AgentDraft,
    AgentDraftRequest,
    AgentDto,
    AgentPage,
    HostedSourceDto,
)
from ...services.agent_access import (
    enforce_decision,
    load_visible_agent,
    project_in_org,
    require_agent_reader,
)
from ...services.agent_evals import gate
from ...services.agent_governance import (
    Action,
    decide,
    is_member,
    may_create_somewhere,
    visible_clause,
)
from ...services.agent_releases import (
    after_publish,
)
from ...services.agent_social import auto_watch
from ...services.agent_views import agent_dto
from ...services.agents import (
    digest,
    require_complete,
    version_errors,
)
from ...services.egress import egress_gate_for_remote_agent
from ...services.hosted_images import pin_hosted_runtime
from ...services.hosted_sources import (
    MAX_BYTES,
    SourceArchive,
    SourceRefused,
    dependency_file,
    load_source,
    store_source,
)
from ...services.memory_access import check_bindings_at_publish
from ...services.network import check_agent_network
from ...services.organisations import organisation_by_slug
from ...services.remote_agents import pin_remote_runtime
from ._common import _grants_bound, _model_allowed, _skills_usable
from ._router import Slug, router


@router.get("/orgs/{org}/agents", response_model=AgentPage, operation_id="listAgents")
async def list_agents(  # noqa: PLR0913 - one parameter per console filter
    org: str,
    session: Db,
    principal: Me,
    page: Pagination,
    scope: Annotated[Literal["platform", "org", "project", "personal"] | None, Query()] = None,
    project: Annotated[str | None, Query()] = None,
    tag: Annotated[list[str] | None, Query(description="every tag given (AND)")] = None,
    starred: Annotated[bool, Query(description="only the agents the caller has starred")] = False,
    sort: Annotated[Literal["name", "stars"], Query()] = "name",
    retired: Annotated[bool, Query(description="include the retired agents")] = True,
    awaiting_approval: Annotated[
        bool, Query(description="only agents with a release awaiting approval")
    ] = False,
    q: Annotated[str | None, Query(max_length=200, description="a substring of the slug or the name")] = None,
) -> AgentPage:
    """The agents the caller may see (ADR 0061), one page at a time (ADR 0206): by name, or most starred
    first with ties by name. Optionally of one scope or one project, matching `q`, and carrying every tag
    given (S40-10; applied after visibility, so a tag never reveals an invisible agent)."""
    require_agent_reader(principal, org)
    organisation = await organisation_by_slug(session, org)
    agent_query = select(Agent).where(
        Agent.org_id == organisation.id, await visible_clause(session, principal, organisation)
    )
    if scope is not None:
        agent_query = agent_query.where(Agent.scope == scope)
    if project is not None:
        agent_query = agent_query.where(
            Agent.project_id == (await project_in_org(session, organisation, project)).id
        )
    if starred:
        # Never reveals an invisible agent: the star only narrows what the visibility clause allows.
        agent_query = agent_query.where(
            Agent.id.in_(select(AgentStar.agent_id).where(AgentStar.user_id == principal.user_id))
        )
    if not retired:
        agent_query = agent_query.where(Agent.lifecycle != "retired")
    if awaiting_approval:
        agent_query = agent_query.where(
            Agent.id.in_(select(AgentRelease.agent_id).where(AgentRelease.status == "pending_approval"))
        )
    if q and q.strip():
        needle = q.strip()
        agent_query = agent_query.where(
            or_(
                Agent.slug.icontains(needle, autoescape=True),
                Agent.display_name.icontains(needle, autoescape=True),
            )
        )
    for wanted_tag in tag or []:
        agent_query = agent_query.where(has_tag(Agent.tags, wanted_tag))
    if sort == "stars":
        star_count = select(func.count(AgentStar.id)).where(AgentStar.agent_id == Agent.id).scalar_subquery()
        starred_rows, meta = await keyset_page(
            session,
            agent_query.add_columns(star_count.label("star_count")),
            sort=star_count,
            tiebreak=Agent.slug,
            page=page,
            tiebreak_descending=False,
        )
        agents = [row.Agent for row in starred_rows]
    else:
        agents, meta = await keyset_page(
            session,
            agent_query,
            sort=Agent.slug,
            tiebreak=Agent.id,
            page=page,
            descending=False,
            tiebreak_descending=False,
        )
    return AgentPage(items=[await agent_dto(session, agent, principal, org) for agent in agents], meta=meta)


@router.post(
    "/orgs/{org}/agents",
    response_model=AgentDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createAgent",
)
async def create_agent(
    org: str, body: AgentCreate, session: Db, principal: Me, allow_egress: bool = False
) -> AgentDto:
    enforce_decision(
        decide(principal, org, None, Action.CREATE, project_slug=body.project, target_scope=body.scope)
    )
    if body.kind == "external" and body.scope != "org":
        raise unprocessable("an external agent carries an organisation credential: it is an `org` agent")
    version_errors(body.spec)
    # Its own eval suite gates its version 1 (S32-03).
    gate(body.spec, None)
    organisation = await organisation_by_slug(session, org)
    await require_complete(session, organisation.id, body.kind, body.spec)
    await check_agent_network(session, organisation.id, body.spec)
    project_row = await project_in_org(session, organisation, body.project) if body.project else None
    # A remote agent's card is read and pinned now (ADR 0062); any other spec comes back as it is.
    await egress_gate_for_remote_agent(session, principal, organisation, body.spec, allow_egress)
    body.spec = await pin_remote_runtime(session, organisation.id, body.spec)
    body.spec = await pin_hosted_runtime(session, organisation.id, body.spec)
    await _skills_usable(session, organisation, body.spec)
    await _model_allowed(session, organisation, body.spec)
    await _grants_bound(session, organisation, body.spec)
    await check_bindings_at_publish(
        session,
        principal,
        organisation,
        body.spec,
        project_slug=body.project if body.scope == "project" else None,
    )
    existant = (
        await session.execute(
            select(Agent.id).where(Agent.org_id == organisation.id, Agent.slug == body.slug)
        )
    ).first()
    if existant is not None:
        raise conflict(f"agent `{body.slug}` already exists in {org}")
    agent = Agent(
        org_id=organisation.id,
        slug=body.slug,
        kind=body.kind,
        display_name=body.display_name,
        description=body.description,
        tags=list(body.tags),
        owner_id=principal.user_id,
        status="active",
        lifecycle=body.lifecycle,
        scope=body.scope,
        project_id=project_row.id if project_row else None,
        release_policy=body.release_policy.model_dump(mode="json") if body.release_policy else None,
    )
    session.add(agent)
    await session.flush()
    session.add(
        AgentVersion(
            agent_id=agent.id,
            org_id=organisation.id,
            version=1,
            spec=body.spec.model_dump(mode="json"),
            checksum=digest(body.spec),
            created_by=principal.email,
            message=(body.message or "").strip() or "Created",
        )
    )
    await session.flush()
    await after_publish(session, principal, agent, 1, principal.email)
    if principal.kind == "user":
        await auto_watch(session, agent)
    await record(
        session,
        principal,
        "agent.create",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
        scope=agent.scope,
        project=body.project,
        tags=agent.tags,
    )
    return await agent_dto(session, agent, principal, org, with_versions=True)


@router.post("/orgs/{org}/agent-drafts", response_model=AgentDraft, operation_id="draftAgent")
async def draft_agent(org: str, body: AgentDraftRequest, session: Db, principal: Me) -> AgentDraft:
    """One capped model call proposes an agent from its description (S39-05). Nothing is saved: the
    console fills the builder with the draft, and a person creates the agent."""
    from ...services import agent_drafts

    if not principal.can(Permission.AGENT_MANAGE, org):
        raise forbidden("drafting an agent needs the agent:manage permission")
    admitted, wait = agent_drafts.limiter.admits(f"{org}:{principal.user_id}")
    if not admitted:
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many drafts",
            f"more than {agent_drafts.PER_MINUTE} drafts per minute",
            retry_after=wait,
        )
    organisation = await organisation_by_slug(session, org)
    try:
        proposal = await agent_drafts.draft(session, organisation, body, principal)
    except agent_drafts.DraftingError as exc:
        raise upstream("gateway", str(exc)) from exc
    await record(
        session,
        principal,
        "agent.draft",
        org_id=organisation.id,
        model=proposal.model,
        description_chars=len(body.description),
        notes=len(proposal.notes),
    )
    return proposal


def _source_dto(archive: SourceArchive) -> HostedSourceDto:
    return HostedSourceDto(
        sha256=archive.sha256,
        size_bytes=archive.size_bytes,
        files=list(archive.files),
        dependencies=dependency_file(archive),
    )


@router.post(
    "/orgs/{org}/hosted-sources",
    response_model=HostedSourceDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="uploadHostedSource",
)
async def upload_hosted_source(org: str, request: Request, session: Db, principal: Me) -> HostedSourceDto:
    """A hosted agent's source zip (ADR 0080 §9), checked and stored under its sha256, which a zip
    version's `source.sha256` then names. The same bytes uploaded twice are one source."""
    if not may_create_somewhere(principal, org):
        raise forbidden("uploading a hosted source asks the right to create an agent in the organisation")
    organisation = await organisation_by_slug(session, org)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_BYTES:
            raise unprocessable(f"the archive is larger than {MAX_BYTES // (1024 * 1024)} MiB")
    try:
        archive = await store_source(object_store(), organisation.id, bytes(data))
    except SourceRefused as exc:
        raise unprocessable(str(exc)) from exc
    except ObjectStoreError as exc:
        raise upstream("object-store", str(exc)) from exc
    await record(
        session,
        principal,
        "agent.hosted_source_upload",
        org_id=organisation.id,
        target_type="hosted_source",
        # `target_id` is 64 characters: the hex digest fits it, its `sha256:` prefix does not.
        target_id=archive.sha256.removeprefix("sha256:"),
        size_bytes=archive.size_bytes,
        files=len(archive.files),
    )
    return _source_dto(archive)


@router.get(
    "/orgs/{org}/hosted-sources/{sha256}",
    response_model=HostedSourceDto,
    operation_id="getHostedSource",
)
async def get_hosted_source(org: str, sha256: str, session: Db, principal: Me) -> HostedSourceDto:
    if not is_member(principal, org):
        raise forbidden("not a member of the organisation")
    organisation = await organisation_by_slug(session, org)
    try:
        return _source_dto(await load_source(object_store(), organisation.id, sha256))
    except SourceRefused as exc:
        raise not_found("Hosted source", sha256) from exc
    except ObjectStoreError as exc:
        raise upstream("object-store", str(exc)) from exc


@router.get("/orgs/{org}/agents/{slug}", response_model=AgentDto, operation_id="getAgent")
async def get_agent(org: str, slug: Slug, session: Db, principal: Me) -> AgentDto:
    _, agent, _ = await load_visible_agent(session, principal, org, slug)
    return await agent_dto(session, agent, principal, org, with_versions=True)
