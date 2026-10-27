# SPDX-License-Identifier: Apache-2.0
"""An organisation's outbound proxy for its web search and web reading (S40-26b, ADR 0087).

`org_admin` only, on all four routes. The password is sealed (ADR 0058), write-only and never in an
answer or an audit row; the proxy host is NOT added to the egress allowlist (ADR 0087 §3).

One more route, for the in-cluster relay only (S40-26d, ADR 0091): `GET /internal/orgs/{org}/outbound-proxy/
{kind}`, authenticated by a relay JWT for that one organisation. It is the one answer with the password in
clear."""

from __future__ import annotations

from typing import Annotated

import jwt
from choregos_contracts import Role
from choregos_core import utcnow
from choregos_core.outbound_proxy import OutboundProxyError
from choregos_core.redaction import remember_secret
from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Organization
from ..deps import Db, DbPlateforme, Me
from ..errors import ApiError, forbidden, not_found, unauthorized, unprocessable
from ..logging import get_logger
from ..rbac import Principal
from ..schemas.outbound_proxy import (
    OutboundProxyPut,
    OutboundProxyState,
    OutboundProxyTest,
    ProxyKind,
    RelayOutboundProxy,
    RelayProxyCredentials,
)
from ..security import verify_relay_token
from ..services import outbound_proxy as service
from ..services.connector_secrets import put_values, require_store

router = APIRouter(tags=["admin"])
log = get_logger(__name__)

_SECRET_PREFIX, _, _SECRET_FIELD = service.SECRET_NAME.rpartition("/")


async def _admin_organisation(session: AsyncSession, principal: Principal, org: str) -> Organization:
    if principal.org_roles.get(org) != Role.ORG_ADMIN:
        raise forbidden(f"the outbound proxy of {org} is managed by its org_admin")
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", org)
    return organisation


@router.get("/orgs/{org}/outbound-proxy", response_model=OutboundProxyState, operation_id="getOutboundProxy")
async def get_outbound_proxy(org: str, session: Db, principal: Me) -> OutboundProxyState:
    """The organisation's proxy, or `proxy: null`. The password is never returned, only `password_set`."""
    return await service.state_of(session, await _admin_organisation(session, principal, org))


@router.put("/orgs/{org}/outbound-proxy", response_model=OutboundProxyState, operation_id="putOutboundProxy")
async def put_outbound_proxy(
    org: str, body: OutboundProxyPut, session: Db, principal: Me
) -> OutboundProxyState:
    """Sets the one proxy. `password` is sealed; absent, the stored one is kept (422 on the first write)."""
    organisation = await _admin_organisation(session, principal, org)
    port = int(body.endpoint.rpartition(":")[2])
    if not 1 <= port <= 65535:
        raise unprocessable(
            "the endpoint's port is out of range",
            [{"loc": ["body", "endpoint"], "msg": "the port must be between 1 and 65535"}],
        )
    store = require_store({"password": body.password} if body.password else None)
    if body.password is None:
        have = await store.meta(session, organisation.id, [service.SECRET_NAME])
        if service.stored(organisation) is None or service.SECRET_NAME not in have:
            raise unprocessable(
                "the first write needs a password",
                [{"loc": ["body", "password"], "msg": "required when no proxy is stored yet"}],
            )
    else:
        remember_secret(body.password)
        await put_values(
            session, store, organisation.id, _SECRET_PREFIX, {_SECRET_FIELD: body.password}, principal.email
        )
    now = utcnow().isoformat()
    settings = dict(organisation.settings or {})
    settings[service.SETTINGS_KEY] = {
        "proxy": {
            "enabled": body.enabled,
            "endpoint": body.endpoint,
            "scheme": body.scheme,
            "username": body.username,
            "applies_to": sorted(set(body.applies_to)),
        },
        "updated_by": principal.email,
        "updated_at": now,
    }
    organisation.settings = settings
    await session.flush()
    await record(
        session,
        principal,
        "outbound_proxy.update",
        org_id=organisation.id,
        target_type="organization",
        target_id=organisation.id,
        endpoint=body.endpoint,
        scheme=body.scheme,
        username=body.username,
        enabled=body.enabled,
        applies_to=sorted(set(body.applies_to)),
        password_changed=body.password is not None,
    )
    return await service.state_of(session, organisation, store)


@router.delete(
    "/orgs/{org}/outbound-proxy",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteOutboundProxy",
)
async def delete_outbound_proxy(org: str, session: Db, principal: Me) -> Response:
    """Removes the setting and its sealed password. Idempotent."""
    organisation = await _admin_organisation(session, principal, org)
    before = service.stored(organisation)
    settings = dict(organisation.settings or {})
    settings.pop(service.SETTINGS_KEY, None)
    organisation.settings = settings
    from ..secret_store import get_secret_store

    await get_secret_store().delete(session, organisation.id, service.SECRET_NAME)
    await session.flush()
    proxy = (before or {}).get("proxy") or {}
    await record(
        session,
        principal,
        "outbound_proxy.delete",
        org_id=organisation.id,
        target_type="organization",
        target_id=organisation.id,
        endpoint=proxy.get("endpoint"),
        username=proxy.get("username"),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/orgs/{org}/outbound-proxy/test", response_model=OutboundProxyTest, operation_id="testOutboundProxy"
)
async def test_outbound_proxy(org: str, session: Db, principal: Me) -> OutboundProxyTest:
    """Fetches `https://ipinfo.io/json` through the stored proxy (ignoring `enabled`). A failing proxy is
    a 200 with `ok: false`; 404 when none is set."""
    organisation = await _admin_organisation(session, principal, org)
    found = service.stored(organisation)
    if found is None:
        raise not_found("Outbound proxy", org)
    try:
        proxy = await service.stored_config(session, organisation)
    except OutboundProxyError as lost:
        result = OutboundProxyTest(ok=False, error=str(lost), elapsed_ms=0, target=service.TEST_TARGET)
    else:
        assert proxy is not None
        result = await service.run_test(proxy)
    await record(
        session,
        principal,
        "outbound_proxy.test",
        org_id=organisation.id,
        target_type="organization",
        target_id=organisation.id,
        endpoint=found["proxy"]["endpoint"],
        username=found["proxy"]["username"],
        ok=result.ok,
    )
    return result


def relay_org(org: str, authorization: Annotated[str | None, Header()] = None) -> str:
    """The relay's credential (ADR 0091 §3): a relay JWT for THIS organisation. A run token, an API token
    or a session is refused: they are for other audiences."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("missing relay token")
    try:
        org_of_token = verify_relay_token(authorization.split(" ", 1)[1].strip())
    except jwt.PyJWTError as exc:
        raise forbidden(f"invalid relay token: {exc}") from exc
    if org_of_token != org:
        raise forbidden("this relay token belongs to another organisation")
    return org_of_token


@router.get(
    "/internal/orgs/{org}/outbound-proxy/{kind}",
    response_model=RelayOutboundProxy,
    operation_id="getRelayOutboundProxy",
    tags=["internal"],
)
async def get_relay_outbound_proxy(
    kind: ProxyKind,
    response: Response,
    session: DbPlateforme,
    org: Annotated[str, Depends(relay_org)],
) -> RelayOutboundProxy:
    """The proxy `kind` of the organisation's traffic goes through, or `proxy: null` (direct). 503 when
    one applies and cannot be built: the relay then refuses, it never goes direct. Not audited, logged
    without the password (ADR 0091 §7)."""
    response.headers["Cache-Control"] = "no-store"
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", org)
    try:
        proxy = await service.outbound_proxy_for(session, organisation.id, kind)
    except OutboundProxyError as lost:
        log.warning("relay_proxy_unavailable", org=org, kind=kind)
        raise ApiError(status.HTTP_503_SERVICE_UNAVAILABLE, "Outbound proxy unavailable", str(lost)) from lost
    log.info("relay_proxy_resolved", org=org, kind=kind, proxied=proxy is not None)
    if proxy is None:
        return RelayOutboundProxy(proxy=None)
    return RelayOutboundProxy(
        proxy=RelayProxyCredentials(
            endpoint=proxy.endpoint, scheme=proxy.scheme, username=proxy.username, password=proxy.password
        )
    )
