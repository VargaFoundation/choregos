# SPDX-License-Identifier: Apache-2.0
"""Installing agents from the catalogue (ADR 0040)."""

from __future__ import annotations

from choregos_core import utcnow
from fastapi import Response, status
from sqlalchemy import select

from ...audit import record
from ...db.models import (
    Agent,
    AgentCredential,
    ApiToken,
)
from ...deps import Config, Db, Me
from ...errors import conflict, forbidden, not_found
from ...schemas import (
    AgentCatalogueConnect,
    AgentCatalogueConnection,
    AgentCatalogueEntry,
    AgentCatalogueInstall,
    AgentDto,
    ApiTokenCreated,
)
from ...services.agent_access import (
    agent_in_org,
    require_agent_reader,
)
from ...services.agent_views import agent_dto
from ...services.api_tokens import api_token_dto
from ...services.organisations import organisation_by_slug
from ._common import _admin
from ._router import Slug, router


@router.get(
    "/orgs/{org}/agent-catalogue",
    response_model=list[AgentCatalogueEntry],
    operation_id="listAgentCatalogue",
)
async def list_agent_catalogue(
    org: str, session: Db, principal: Me, settings: Config
) -> list[AgentCatalogueEntry]:
    """The agents the platform offers, and where they stand in the organisation: installed, in
    which version, an update available — or an agent of the organisation that carries that name."""
    from ...services import agent_catalogue as catalogue

    require_agent_reader(principal, org)
    organisation = await organisation_by_slug(session, org)
    rendered = []
    for proposal in (await catalogue.catalogue_of(session, organisation.id)).entries:
        offer_state = await catalogue.state(session, organisation.id, proposal)
        is_offered, reason_text = catalogue.offer(proposal, settings)
        rendered.append(
            AgentCatalogueEntry(
                slug=proposal.slug,
                kind=proposal.kind,
                display_name=proposal.document["display_name"],
                description=proposal.document.get("description"),
                role=proposal.role,
                summary=proposal.summary,
                version=proposal.version,
                skills=list(proposal.skills),
                tags=list(proposal.tags),
                client=proposal.client,
                reach=proposal.reach,
                installed=offer_state.installed,
                installed_version=offer_state.installed_version,
                own_agent=offer_state.installed and not offer_state.catalogue_agent,
                scope=offer_state.scope,
                update_available=offer_state.update_available,
                offered=is_offered,
                unavailable_reason=reason_text,
            )
        )
    return rendered


@router.post(
    "/orgs/{org}/agent-catalogue/{slug}/install",
    response_model=AgentDto,
    operation_id="installCatalogueAgent",
)
async def install_catalogue_agent(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    response: Response,
    body: AgentCatalogueInstall | None = None,
) -> AgentDto:
    """Install the agent as version 1 (201); already installed, `upgrade` publishes the next version if the
    catalogue changed (200). An agent of the organisation with the same name is never touched (409)."""
    from ...services import agent_catalogue as catalogue

    _admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    view = await catalogue.catalogue_of(session, organisation.id)
    proposal = view.entry(slug)
    if proposal is None:
        raise not_found("Catalogue agent", slug)
    offer_state = await catalogue.state(session, organisation.id, proposal)
    if not offer_state.installed:
        await catalogue.install(session, organisation.id, proposal, principal, view)
        response.status_code = status.HTTP_201_CREATED
    elif not offer_state.catalogue_agent:
        raise conflict(
            f"`{slug}` is an agent of {org} that does not come from the catalogue: it is left untouched"
        )
    elif not (body and body.upgrade):
        raise conflict(f"`{slug}` is already installed: ask for `upgrade` to publish the catalogue's version")
    elif offer_state.divergent:
        raise conflict(
            f"`{slug}` comes from the catalogue but {org} has published its own text since: the upgrade "
            f"would publish catalogue version {proposal.version} over it, so it is left untouched. "
            "Publish the catalogue's text as the next version by hand (`POST …/agents/<slug>/versions`) "
            "if that is what you want"
        )
    else:
        await catalogue.upgrade(session, organisation.id, proposal, principal, view)
    return await agent_dto(
        session, await agent_in_org(session, organisation, slug), principal, org, with_versions=True
    )


@router.post(
    "/orgs/{org}/agent-catalogue/{slug}/connect",
    response_model=AgentCatalogueConnection,
    status_code=status.HTTP_201_CREATED,
    operation_id="connectCatalogueClient",
)
async def connect_catalogue_client(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    settings: Config,
    body: AgentCatalogueConnect | None = None,
) -> AgentCatalogueConnection:
    """Connect an MCP door client in one click (ADR 0040): its external agent — created by an
    administrator the first time, then joined by each member —, then an `mcp:*` token
    minted for the person and attached to the agent, returned ONCE. For a client that calls from
    its vendor's cloud, no token: the OAuth client the IdP registered is attached."""
    from datetime import timedelta

    from ...security import generate_api_token
    from ...services import agent_catalogue as catalogue

    connect_body = body or AgentCatalogueConnect()
    require_agent_reader(principal, org)
    if principal.kind != "user":
        raise forbidden("a client is connected by a person, from a session")
    organisation = await organisation_by_slug(session, org)
    proposal = (await catalogue.catalogue_of(session, organisation.id)).entry(slug)
    if proposal is None or proposal.kind != "external":
        raise not_found("Catalogue client", slug)
    is_offered, reason_text = catalogue.offer(proposal, settings)
    if not is_offered:
        raise conflict(f"`{slug}` is not offered here: {reason_text}")
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == organisation.id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None:
        _admin(principal, org)
        await catalogue.install(session, organisation.id, proposal, principal)
        agent = await agent_in_org(session, organisation, slug)
    elif agent.kind != "external":
        raise conflict(f"`{slug}` is an internal agent of {org}: it cannot carry a client")
    api_token = None
    oauth_client_id = None
    if proposal.reach == "cloud":
        oauth_client_id = settings.mcp_oauth_clients[str(proposal.client)].client_id
        already_linked = (
            await session.execute(
                select(AgentCredential.id).where(
                    AgentCredential.org_id == organisation.id, AgentCredential.client_id == oauth_client_id
                )
            )
        ).first()
        if already_linked is None:
            session.add(AgentCredential(org_id=organisation.id, agent_id=agent.id, kind="oauth_client",
                                        client_id=oauth_client_id, created_by=principal.email))  # fmt: skip
    else:
        raw_token, token_hash = generate_api_token()
        db_row = ApiToken(
            user_id=principal.user_id,
            name=str(proposal.document["display_name"]),
            hash=token_hash,
            scopes=["mcp:read"] if connect_body.read_only else ["mcp:write"],
            expires_at=utcnow() + timedelta(days=connect_body.expires_in_days)
            if connect_body.expires_in_days
            else None,
        )
        session.add(db_row)
        await session.flush()
        session.add(AgentCredential(org_id=organisation.id, agent_id=agent.id, kind="token",
                                    api_token_id=db_row.id, created_by=principal.email))  # fmt: skip
        await record(session, principal, "token.create", org_id=None, target_type="api_token",
                     target_id=db_row.id, scopes=list(db_row.scopes or []))  # fmt: skip
        api_token = ApiTokenCreated(**api_token_dto(db_row).model_dump(), token=raw_token)
    await session.flush()
    await record(session, principal, "agent.credential.attach", org_id=organisation.id, target_type="agent",
                 target_id=agent.id, via="catalogue")  # fmt: skip
    return AgentCatalogueConnection(
        agent=await agent_dto(session, agent, principal, org, with_versions=True),
        token=api_token,
        oauth_client_id=oauth_client_id,
    )
