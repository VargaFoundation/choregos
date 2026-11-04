# SPDX-License-Identifier: Apache-2.0
"""An organisation's MCP gateway (S46-07, S46-08, ADR 0105): its setting, its test and its assertion keys.

`GET`, `PUT`, `DELETE` and `POST .../test` of `/orgs/{org}/mcp-gateway` are `org_admin` only. The
credential is sealed, write-only, and in no answer, log or audit row. `POST .../assertion-key` is
`org_admin` only. `GET .../jwks.json` is public: the gateway reads it to verify what Choregos signs. It
answers the same 404 for an unknown organisation and for one without a key. Limit: `OrgAliasMiddleware`
adds a `Link: rel="canonical"` header to every answer for a former slug, so a keyless organisation
reached by its old slug is distinguishable from an unknown slug by that header (not this route's doing).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from choregos_adapters.mcp import McpError
from choregos_contracts import Role
from choregos_core import utcnow
from choregos_core.redaction import redact, remember_secret
from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import get_settings
from ..db.models import Organization, OrgConnector
from ..deps import Db, Me, PlatformDb
from ..errors import conflict, forbidden, not_found
from ..rbac import Principal
from ..schemas.mcp_gateway import (
    McpGatewayAssertion,
    McpGatewayPut,
    McpGatewayState,
    McpGatewayTest,
    McpGatewayTestRequest,
)
from ..secret_store import get_secret_store
from ..services import mcp_gateway as service
from ..services import mcp_gateway_identity as identity
from ..services.connector_secrets import put_values, require_store
from ..services.egress import egress_scope, ensure_or_409, revoke_rules_of_origin
from ..services.organisations import organisation_by_slug
from ..services.platform_network import check_platform_host

router = APIRouter(tags=["admin"])

_SECRET_PREFIX, _, _SECRET_FIELD = service.SECRET_NAME.rpartition("/")
_PURPOSE = "the organisation's MCP gateway"


async def _admin_organisation(session: AsyncSession, principal: Principal, org: str) -> Organization:
    if principal.org_roles.get(org) != Role.ORG_ADMIN:
        raise forbidden(f"the MCP gateway of {org} is managed by its org_admin")
    return await organisation_by_slug(session, org)


@router.get("/orgs/{org}/mcp-gateway", response_model=McpGatewayState, operation_id="getMcpGateway")
async def get_mcp_gateway(org: str, session: Db, principal: Me) -> McpGatewayState:
    """The organisation's gateway, or `gateway: null`, and its assertion keys. The credential is never
    returned, only `secret_set`."""
    return await service.state_of(session, await _admin_organisation(session, principal, org))


async def _direct_connectors(session: AsyncSession, org_id: str) -> list[str]:
    """The organisation's direct `mcp` connectors. A plugin's server is platform-run and in-cluster
    (ADR 0096): it is not a direct upstream, and `required` never applies to it."""
    rows = (
        await session.execute(
            select(OrgConnector).where(
                OrgConnector.org_id == org_id, OrgConnector.kind == "mcp", OrgConnector.type == "mcp"
            )
        )
    ).scalars()
    return sorted(row.name for row in rows if row.plugin_server is None)


@router.put("/orgs/{org}/mcp-gateway", response_model=McpGatewayState, operation_id="putMcpGateway")
async def put_mcp_gateway(org: str, body: McpGatewayPut, session: Db, principal: Me) -> McpGatewayState:
    """Sets the one gateway. `secret` is sealed; absent, the stored one is kept. Every check runs before
    the first write: the setting, the secrets key, the network bounds, then the egress allowlist."""
    organisation = await _admin_organisation(session, principal, org)  # (a)
    internal = get_settings().mcp_gateway_internal_hosts
    service.check_endpoint(body.endpoint, body.routing, internal)  # (b)
    if body.auth.token_url:
        service.check_token_url(body.auth.token_url, internal)
    known = await get_secret_store().meta(session, organisation.id, [service.SECRET_NAME])
    service.validate_put(body, service.stored(organisation), service.SECRET_NAME in known)
    signs = body.auth.type == "jwt_assertion" or bool(body.identity_header)
    store = require_store({"credential": body.secret} if body.secret else None)  # (c)
    if signs:
        require_store({"assertion_key": "x"})  # a 409 without a secrets key, before any write
    setting = service.GatewaySetting.from_put(body)
    await check_platform_host(  # (d)
        session, organisation.id, body.endpoint.replace(service.SERVER_PLACEHOLDER, "x"), _PURPOSE
    )
    if body.auth.token_url:
        await check_platform_host(session, organisation.id, body.auth.token_url, _PURPOSE)
    await ensure_or_409(  # (e)
        session,
        principal,
        organisation,
        service.egress_targets(setting),
        allow_egress=body.allow_egress,
        origin=service.ORIGIN,
        reason=_PURPOSE,
    )
    if body.secret is not None:  # (f)
        remember_secret(body.secret)
        await put_values(
            session, store, organisation.id, _SECRET_PREFIX, {_SECRET_FIELD: body.secret}, principal.email
        )
    elif body.auth.type == "jwt_assertion":
        await store.delete(session, organisation.id, service.SECRET_NAME)
    if signs:  # (g)
        await identity.ensure_assertion_key(session, organisation, principal.user_id, store)
    settings = dict(organisation.settings or {})  # (h), after (g) wrote its own key
    settings[service.SETTINGS_KEY] = {
        "gateway": setting.to_stored(),
        "updated_by": principal.email,
        "updated_at": utcnow().isoformat(),
    }
    organisation.settings = settings
    await session.flush()
    await revoke_rules_of_origin(
        session, principal, organisation, service.ORIGIN, keep=service.hosts_in_use(setting)
    )
    service.clear_token_cache()  # (i)
    direct = await _direct_connectors(session, organisation.id) if body.mode == "required" else []
    await record(  # (j)
        session,
        principal,
        "mcp_gateway.update",
        org_id=organisation.id,
        target_type="organization",
        target_id=organisation.id,
        mode=body.mode,
        endpoint=body.endpoint,
        routing=body.routing,
        server_header=body.server_header,
        auth_type=body.auth.type,
        header_name=body.auth.header_name,
        token_url=body.auth.token_url,
        client_id=body.auth.client_id,
        audience=body.audience,
        identity_header=body.identity_header,
        timeout_s=body.timeout_s,
        secret_changed=body.secret is not None,
        direct_mcp_connectors=direct,
    )
    return await service.state_of(session, organisation, store)


@router.delete(
    "/orgs/{org}/mcp-gateway", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteMcpGateway"
)
async def delete_mcp_gateway(org: str, session: Db, principal: Me) -> Response:
    """Removes the setting and its sealed credential, unless connectors still go through it. Idempotent;
    the assertion keys are kept."""
    organisation = await _admin_organisation(session, principal, org)
    serving = sorted(
        (
            await session.execute(
                select(OrgConnector.name).where(
                    OrgConnector.org_id == organisation.id, OrgConnector.type == service.GATEWAY_TYPE
                )
            )
        ).scalars()
    )
    if serving:
        raise conflict(
            f"the MCP gateway of {org} still serves connectors: delete them first",
            extra={"code": "gateway_in_use", "connectors": serving},
        )
    before = service.stored(organisation)
    settings = dict(organisation.settings or {})
    settings.pop(service.SETTINGS_KEY, None)
    organisation.settings = settings
    await get_secret_store().delete(session, organisation.id, service.SECRET_NAME)
    await session.flush()
    await revoke_rules_of_origin(session, principal, organisation, service.ORIGIN, keep=set())
    service.clear_token_cache()
    await record(
        session,
        principal,
        "mcp_gateway.delete",
        org_id=organisation.id,
        target_type="organization",
        target_id=organisation.id,
        endpoint=before.endpoint if before else None,
        auth_type=before.auth.type if before else None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/orgs/{org}/mcp-gateway/test", response_model=McpGatewayTest, operation_id="testMcpGateway")
async def test_mcp_gateway(
    org: str, body: McpGatewayTestRequest, session: Db, principal: Me
) -> McpGatewayTest:
    """Opens an MCP session with one server through the stored gateway (whatever its mode) and lists its
    tools. A gateway that fails is a 200 with `ok: false`; 404 when none is set."""
    organisation = await _admin_organisation(session, principal, org)
    setting = service.stored(organisation)
    if setting is None:
        raise not_found("MCP gateway", org)
    who = identity.CallIdentity(
        org=organisation.slug,
        org_id=organisation.id,
        subject=f"person:{principal.email}",
        purpose="test",
        server=body.server,
    )
    started = time.monotonic()
    client: Any = None
    result: dict[str, Any] = {}
    error: str | None = None
    try:
        async with egress_scope(session, organisation, "api", "mcp-gateway:test"):
            client = await service.client_for_server(session, organisation, body.server, who)
            result = await asyncio.wait_for(client.test(), setting.timeout_s)
    except TimeoutError:
        error = f"no answer within {setting.timeout_s:g} s"
    except (McpError, httpx.HTTPError, identity.GatewayRefusal) as failed:
        error = redact(str(failed)) or type(failed).__name__
    info = result.get("server")
    tools = result.get("tools")
    tested = McpGatewayTest(
        ok=error is None,
        server=body.server,
        protocol_version=getattr(client, "negotiated_version", None),
        server_info=info if isinstance(info, dict) else None,
        tools=tools if isinstance(tools, int) else None,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        error=None if error is None else f"the MCP gateway of {org}: {error}",
    )
    await record(
        session,
        principal,
        "mcp_gateway.test",
        org_id=organisation.id,
        target_type="organization",
        target_id=organisation.id,
        server=body.server,
        ok=tested.ok,
    )
    return tested


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
async def get_jwks(org: str, session: PlatformDb) -> Response:
    """The organisation's public assertion keys, unauthenticated. One 404 for an unknown organisation
    and for one without a key. Reads `Organization.settings` only, never the secret store."""
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organisation is None or not identity.jwks_of(organisation)["keys"]:
        raise not_found("MCP gateway keys", org)
    return JSONResponse(identity.jwks_of(organisation), headers={"Cache-Control": "public, max-age=300"})
