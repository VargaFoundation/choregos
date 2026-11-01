# SPDX-License-Identifier: Apache-2.0
"""The assertion keys of an organisation's MCP gateway (S46-07, ADR 0105).

`POST .../assertion-key` is `org_admin` only. `GET .../jwks.json` is public: the gateway reads it to
verify what Choregos signs. It answers the same 404 for an unknown organisation and for one without a
key. Limit: `OrgAliasMiddleware` adds a `Link: rel="canonical"` header to every answer for a former
slug, so a keyless organisation reached by its old slug is distinguishable from an unknown slug by
that header (not this route's doing).
"""

from __future__ import annotations

from choregos_contracts import Role
from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Organization
from ..deps import Db, DbPlateforme, Me
from ..errors import forbidden, not_found
from ..rbac import Principal
from ..schemas.mcp_gateway import McpGatewayAssertion
from ..services import mcp_gateway_identity as identity
from ..services.connector_secrets import require_store

router = APIRouter(tags=["admin"])


async def _admin_organisation(session: AsyncSession, principal: Principal, org: str) -> Organization:
    if principal.org_roles.get(org) != Role.ORG_ADMIN:
        raise forbidden(f"the MCP gateway of {org} is managed by its org_admin")
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", org)
    return organisation


@router.post(
    "/orgs/{org}/mcp-gateway/assertion-key",
    response_model=McpGatewayAssertion,
    operation_id="rotateMcpGatewayAssertionKey",
)
async def rotate_assertion_key(org: str, session: Db, principal: Me) -> McpGatewayAssertion:
    """Creates the organisation's assertion key, or rotates it: the previous key stays published."""
    organisation = await _admin_organisation(session, principal, org)
    store = require_store({"assertion_key": "x"})  # a 409 without a secrets key, before any write
    kid, previous = await identity.rotate_assertion_key(session, organisation, principal.user_id, store)
    await record(
        session,
        principal,
        "mcp_gateway.assertion_key.rotate",
        org_id=organisation.id,
        target_type="organization",
        target_id=organisation.id,
        kid=kid,
        previous=previous,
    )
    return identity.assertion_state(organisation)


@router.get("/orgs/{org}/mcp-gateway/jwks.json", response_model=None, operation_id="getMcpGatewayJwks")
async def get_jwks(org: str, session: DbPlateforme) -> Response:
    """The organisation's public assertion keys, unauthenticated. One 404 for an unknown organisation
    and for one without a key. Reads `Organization.settings` only, never the secret store."""
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organisation is None or not identity.jwks_of(organisation)["keys"]:
        raise not_found("MCP gateway keys", org)
    return JSONResponse(identity.jwks_of(organisation), headers={"Cache-Control": "public, max-age=300"})
