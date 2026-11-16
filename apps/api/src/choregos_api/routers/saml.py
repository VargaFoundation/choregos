# SPDX-License-Identifier: Apache-2.0
"""SAML 2.0 sign-in, one identity provider per organisation (ADR 0170).

The platform administrator configures the provider (its domains commit the instance); the members sign in
through `GET /saml/{org}/login` and the provider answers `POST /saml/{org}/acs`. The verification rules are
in `services/saml.py`; the session is opened by `terminer_la_connexion(org=...)`, as for any tenant provider.
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, select

from ..audit import record
from ..config import get_settings
from ..db.models import Organization, OrgSamlIdp, SamlConsumedAssertion
from ..deps import Me, PlatformDb, require_platform_admin
from ..errors import conflict, not_found, unprocessable
from ..schemas.saml import SamlProvider, SamlProviderIn
from ..security import read_session, sign_session
from ..services import saml as saml_service
from ..services.organisations import organisation_by_slug
from ..tenant_sign_in import SignInHint, resolve_extension
from .auth import GROUP_ROLES, redirection_sure, terminer_la_connexion

router = APIRouter(tags=["saml"])

COOKIE = "choregos_saml"
COOKIE_PURPOSE = "saml-request"
DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


async def _provider(session: Any, slug: str) -> tuple[Organization, OrgSamlIdp]:
    organisation = await organisation_by_slug(session, slug)
    row = (
        await session.execute(select(OrgSamlIdp).where(OrgSamlIdp.org_id == organisation.id))
    ).scalar_one_or_none()
    if row is None:
        raise not_found("SAML provider", slug)
    return organisation, row


def _normalise(domains: list[str]) -> list[str]:
    cleaned = sorted({d.strip().lower().rstrip(".") for d in domains})
    bad = [d for d in cleaned if not DOMAIN.match(d)]
    if bad:
        raise unprocessable(f"not a domain name: {bad}")
    return cleaned


async def _require_free_domains(session: Any, organisation: Organization, domains: list[str]) -> None:
    """One domain belongs to one organisation, whatever protocol its provider speaks: otherwise one tenant's
    provider could vouch for another tenant's addresses."""
    rows = (
        await session.execute(
            select(OrgSamlIdp.org_id, OrgSamlIdp.domains).where(OrgSamlIdp.org_id != organisation.id)
        )
    ).all()
    taken = {d for _, held in rows for d in (held or [])}
    for domain in domains:
        owner = await resolve_extension(session, SignInHint(email_domain=domain))
        if domain in taken or (owner is not None and owner.org != organisation.slug):
            raise conflict(f"the domain {domain!r} already belongs to another organisation")


def _view(slug: str, row: OrgSamlIdp | None) -> SamlProvider:
    ours = {"sp_entity_id": saml_service.sp_entity_id(slug), "acs_url": saml_service.acs_url(slug)}
    if row is None:
        return SamlProvider(**ours)
    return SamlProvider(
        idp_entity_id=row.idp_entity_id,
        sso_url=row.sso_url,
        certificate_pem=row.certificate_pem,
        email_attribute=row.email_attribute,
        groups_attribute=row.groups_attribute,
        group_map=dict(row.group_map or {}),
        domains=list(row.domains or []),
        **ours,
    )


@router.put("/orgs/{org}/saml", response_model=SamlProvider, operation_id="setOrgSaml")
async def set_saml(org: str, body: SamlProviderIn, session: PlatformDb, principal: Me) -> SamlProvider:
    """Platform administrators only: a provider opens sessions, and its domains commit the instance."""
    await require_platform_admin(session, principal)
    organisation = await organisation_by_slug(session, org)
    try:
        saml_service.check_certificate(body.certificate_pem)
    except ValueError as unreadable:
        raise unprocessable(
            "the provider's certificate cannot be read (an X.509 PEM is expected)"
        ) from unreadable
    unknown = sorted(set(body.group_map.values()) - set(GROUP_ROLES))
    if unknown:
        raise unprocessable(f"unknown role groups: {unknown} (known: {sorted(GROUP_ROLES)})")
    existing = await resolve_extension(session, SignInHint(org=org))
    if existing is not None and existing.oidc is not None:
        raise conflict(f"{org} already signs in through OpenID Connect: remove that provider first")
    domains = _normalise(body.domains)
    await _require_free_domains(session, organisation, domains)
    row = (
        await session.execute(select(OrgSamlIdp).where(OrgSamlIdp.org_id == organisation.id))
    ).scalar_one_or_none() or OrgSamlIdp(org_id=organisation.id)
    row.idp_entity_id, row.sso_url, row.certificate_pem = (
        body.idp_entity_id,
        body.sso_url,
        body.certificate_pem,
    )
    row.email_attribute, row.groups_attribute = body.email_attribute, body.groups_attribute
    row.group_map, row.domains = dict(body.group_map), domains
    session.add(row)
    await session.flush()
    await record(
        session, principal, "org.saml", org_id=organisation.id, target_type="org", target_id=organisation.id,
        idp_entity_id=body.idp_entity_id, domains=domains,
    )  # fmt: skip
    return _view(org, row)


@router.get("/orgs/{org}/saml", response_model=SamlProvider, operation_id="getOrgSaml")
async def get_saml(org: str, session: PlatformDb, principal: Me) -> SamlProvider:
    await require_platform_admin(session, principal)
    organisation = await organisation_by_slug(session, org)
    row = (
        await session.execute(select(OrgSamlIdp).where(OrgSamlIdp.org_id == organisation.id))
    ).scalar_one_or_none()
    return _view(org, row)


@router.delete("/orgs/{org}/saml", status_code=204, operation_id="deleteOrgSaml")
async def delete_saml(org: str, session: PlatformDb, principal: Me) -> Response:
    """Members sign in through the instance's provider again; the organisation may move to OpenID Connect."""
    await require_platform_admin(session, principal)
    organisation = await organisation_by_slug(session, org)
    await session.execute(delete(OrgSamlIdp).where(OrgSamlIdp.org_id == organisation.id))
    await record(
        session,
        principal,
        "org.saml.remove",
        org_id=organisation.id,
        target_type="org",
        target_id=organisation.id,
    )
    return Response(status_code=204)


@router.get("/saml/{org}/metadata", operation_id="samlMetadata")
async def metadata(org: str, session: PlatformDb) -> Response:
    await _provider(session, org)
    entity = saml_service.sp_entity_id(org)
    xml = (
        '<?xml version="1.0"?>'
        f'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" entityID="{entity}">'
        '<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        "<md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>"
        '<md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{saml_service.acs_url(org)}" index="0"/>'
        "</md:SPSSODescriptor></md:EntityDescriptor>"
    )
    return Response(content=xml, media_type="application/samlmetadata+xml")


@router.get("/saml/{org}/login", operation_id="samlLogin")
async def login(
    org: str,
    session: PlatformDb,
    redirect_to: Annotated[str | None, Query()] = None,
    login_hint: Annotated[str | None, Query(max_length=320)] = None,
) -> RedirectResponse:
    """HTTP-Redirect binding: the request goes to the provider, its id stays in a signed cookie: that is the
    id the assertion must cite (`InResponseTo`)."""
    _, row = await _provider(session, org)
    settings = get_settings()
    request_id = saml_service.new_request_id()
    target = redirection_sure(redirect_to, settings)
    state = sign_session(
        {
            "id": request_id,
            "org": org,
            "target": target,
            "exp": int(time.time()) + saml_service.REQUEST_LIFETIME_S,
        },
        purpose=COOKIE_PURPOSE,
    )
    response = RedirectResponse(
        url=saml_service.authn_request(org, row.sso_url, request_id, datetime.now(UTC)), status_code=307
    )
    # The provider's answer is a CROSS-SITE POST: a `Lax` cookie would not travel with it. `None` needs
    # `Secure`, which cannot travel over the plain http of development and tests: `Lax` there.
    remote = settings.env in {"staging", "prod"}
    response.set_cookie(
        COOKIE, state, max_age=saml_service.REQUEST_LIFETIME_S, httponly=True,
        samesite="none" if remote else "lax", secure=remote,
    )  # fmt: skip
    return response


@router.post("/saml/{org}/acs", operation_id="samlAcs")
async def consume(
    org: str,
    request: Request,
    session: PlatformDb,
    SAMLResponse: Annotated[str, Form()],  # noqa: N803 - the protocol's field name
) -> RedirectResponse:
    state = read_session(request.cookies.get(COOKIE, ""), purpose=COOKIE_PURPOSE)
    if state is None or state.get("org") != org:
        raise saml_service.refusal(
            "no request in progress for this organisation (a provider-initiated sign-in is refused)"
        )
    _, row = await _provider(session, org)
    provider = saml_service.Provider(
        idp_entity_id=row.idp_entity_id,
        certificate_pem=row.certificate_pem,
        domains=tuple(row.domains or []),
        email_attribute=row.email_attribute,
        groups_attribute=row.groups_attribute,
        group_map=dict(row.group_map or {}),
    )
    now = datetime.now(UTC)
    identity = saml_service.verify_response(SAMLResponse, provider, org, str(state.get("id") or ""), now)
    # Replay: an id is good once. Rows past their expiry are no longer needed (the window refuses them).
    await session.execute(delete(SamlConsumedAssertion).where(SamlConsumedAssertion.expires_at < now))
    if await session.get(SamlConsumedAssertion, identity.assertion_id) is not None:
        raise saml_service.refusal("assertion already consumed")
    session.add(SamlConsumedAssertion(id=identity.assertion_id, expires_at=identity.expires_at))
    await session.flush()
    response = await terminer_la_connexion(
        session, email=identity.email, nom=identity.name, sujet=f"saml|{org}|{identity.email}",
        groupes=identity.groups, cible=str(state.get("target") or "/"), canal="saml", org=org,
    )  # fmt: skip
    response.delete_cookie(COOKIE)
    return response
