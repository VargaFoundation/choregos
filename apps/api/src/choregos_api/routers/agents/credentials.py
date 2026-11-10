# SPDX-License-Identifier: Apache-2.0
"""Credentials of external agents."""

from __future__ import annotations

from fastapi import Response, status
from sqlalchemy import select

from ...audit import record
from ...db.models import (
    Agent,
    AgentCredential,
    ApiToken,
    Membership,
)
from ...deps import Db, Me
from ...errors import conflict, not_found, unprocessable
from ...schemas import (
    AgentCredentialCreate,
    AgentCredentialDto,
)
from ...services.agent_access import (
    enforce_decision,
    load_visible_agent,
)
from ...services.agent_governance import (
    Action,
    decide,
)
from ...services.organisations import organisation_by_slug
from ._common import _admin
from ._router import Slug, router


def _credential_dto(db_row: AgentCredential, api_token: ApiToken | None = None) -> AgentCredentialDto:
    return AgentCredentialDto(
        id=db_row.id,
        kind=db_row.kind,
        token_id=db_row.api_token_id,
        client_id=db_row.client_id,
        created_by=db_row.created_by,
        created_at=db_row.created_at,
        revoked_at=db_row.revoked_at,
        token_name=api_token.name if api_token else None,
        last_used_at=api_token.last_used_at if api_token else None,
        last_client=api_token.last_client if api_token else None,
    )


@router.get(
    "/orgs/{org}/agent-credentials",
    response_model=list[AgentCredentialDto],
    operation_id="listOrgAgentCredentials",
)
async def list_org_credentials(org: str, session: Db, principal: Me) -> list[AgentCredentialDto]:
    """Every client attached to an agent of the organisation, with its agent (S30-04): the AI
    clients' page read one list per external agent."""
    from ...db.session import in_platform_scope

    _admin(principal, org)
    organisation = await organisation_by_slug(session, org)
    db_rows = (
        await session.execute(
            select(AgentCredential, Agent.slug)
            .join(Agent, Agent.id == AgentCredential.agent_id)
            .where(AgentCredential.org_id == organisation.id)
            .order_by(Agent.slug)
        )
    ).all()
    ids = [db_row.api_token_id for db_row, _ in db_rows if db_row.api_token_id]
    api_tokens: dict[str, ApiToken] = {}
    if ids:
        async with in_platform_scope(session):
            found_tokens = await session.execute(select(ApiToken).where(ApiToken.id.in_(ids)))
            api_tokens = {api_token.id: api_token for api_token in found_tokens.scalars()}
    return [
        _credential_dto(db_row, api_tokens.get(db_row.api_token_id or "")).model_copy(update={"agent": slug})
        for db_row, slug in db_rows
    ]


@router.get(
    "/orgs/{org}/agents/{slug}/credentials",
    response_model=list[AgentCredentialDto],
    operation_id="listAgentCredentials",
)
async def list_credentials(org: str, slug: Slug, session: Db, principal: Me) -> list[AgentCredentialDto]:
    from ...db.session import in_platform_scope

    _, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.MANAGE))
    db_rows = list(
        (await session.execute(select(AgentCredential).where(AgentCredential.agent_id == agent.id))).scalars()
    )
    # The tokens are their humans', outside the organisation's scope: read as at
    # attachment, to say when and with which client each one last called the door.
    ids = [db_row.api_token_id for db_row in db_rows if db_row.api_token_id]
    api_tokens: dict[str, ApiToken] = {}
    if ids:
        async with in_platform_scope(session):
            found_tokens = await session.execute(select(ApiToken).where(ApiToken.id.in_(ids)))
            api_tokens = {api_token.id: api_token for api_token in found_tokens.scalars()}
    return [_credential_dto(db_row, api_tokens.get(db_row.api_token_id or "")) for db_row in db_rows]


@router.post(
    "/orgs/{org}/agents/{slug}/credentials",
    response_model=AgentCredentialDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="attachAgentCredential",
)
async def attach_credential(
    org: str, slug: Slug, body: AgentCredentialCreate, session: Db, principal: Me
) -> AgentCredentialDto:
    """Attach an MCP door client to an `external` agent: its calls then carry the agent,
    and its rights are those of the human intersected with those of the agent's version."""
    from ...db.session import in_platform_scope

    organisation, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.MANAGE))
    if agent.kind != "external":
        raise unprocessable(f"`{slug}` is an internal agent: only an `external` agent carries an MCP client")
    if body.kind == "token":
        if not body.token_id:
            raise unprocessable("`token_id` is missing")
        async with in_platform_scope(session):
            api_token = await session.get(ApiToken, body.token_id)
            # A token is attached only if its owner belongs to this organisation (finding #1520): the
            # id alone let an administrator elsewhere bind another person's token to their agent.
            member = (
                (
                    await session.execute(
                        select(Membership.id)
                        .where(Membership.user_id == api_token.user_id, Membership.org_id == organisation.id)
                        .limit(1)
                    )
                ).first()
                if api_token is not None
                else None
            )
        # One answer for a missing token and another person's: the id reveals nothing.
        if api_token is None or member is None or not set(api_token.scopes or []) & {"mcp:read", "mcp:write"}:
            raise unprocessable("an existing `mcp:read` or `mcp:write` token is expected")
        existant = (
            await session.execute(
                select(AgentCredential.id).where(AgentCredential.api_token_id == api_token.id)
            )
        ).first()
    else:
        if not body.client_id:
            raise unprocessable("`client_id` is missing")
        existant = (
            await session.execute(
                select(AgentCredential.id).where(
                    AgentCredential.org_id == organisation.id, AgentCredential.client_id == body.client_id
                )
            )
        ).first()
    if existant is not None:
        raise conflict("this client is already attached to an agent")
    db_row = AgentCredential(
        org_id=organisation.id,
        agent_id=agent.id,
        kind=body.kind,
        api_token_id=body.token_id if body.kind == "token" else None,
        client_id=body.client_id if body.kind == "oauth_client" else None,
        created_by=principal.email,
    )
    session.add(db_row)
    await session.flush()
    await record(
        session,
        principal,
        "agent.credential.attach",
        org_id=organisation.id,
        target_type="agent",
        target_id=agent.id,
    )
    return _credential_dto(db_row)


@router.delete(
    "/orgs/{org}/agents/{slug}/credentials/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="detachAgentCredential",
)
async def detach_credential(org: str, slug: Slug, credential_id: str, session: Db, principal: Me) -> Response:
    _, agent, subject = await load_visible_agent(session, principal, org, slug)
    enforce_decision(decide(principal, org, subject, Action.MANAGE))
    db_row = await session.get(AgentCredential, credential_id)
    if db_row is None or db_row.agent_id != agent.id:
        raise not_found("Agent client", credential_id)
    await session.delete(db_row)
    await record(
        session,
        principal,
        "agent.credential.detach",
        org_id=agent.org_id,
        target_type="agent",
        target_id=agent.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
