# SPDX-License-Identifier: Apache-2.0
"""Which organisation signs in through a SAML provider of the core (ADR 0170): the core's own answer to the
sign-in resolver of ADR 0108, asked after the extension's."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..db.models import Organization, OrgSamlIdp
from ..db.org_aliases import _platform_scope, organisation_by_slug_or_alias
from ..tenant_sign_in import SignInHint, TenantSignIn


async def saml_sign_in(session: Any, hint: SignInHint) -> TenantSignIn | None:
    """The organisation named in the link (slug or former slug), else the one that owns the typed domain."""
    async with _platform_scope(session):
        if hint.org:
            organisation = await organisation_by_slug_or_alias(session, hint.org)
            if organisation is None:
                return None
            row = (
                await session.execute(select(OrgSamlIdp).where(OrgSamlIdp.org_id == organisation.id))
            ).scalar_one_or_none()
            return _tenant(organisation.slug) if row is not None else None
        if hint.email_domain:
            for slug, domains in (
                await session.execute(
                    select(Organization.slug, OrgSamlIdp.domains).join(
                        Organization, Organization.id == OrgSamlIdp.org_id
                    )
                )
            ).all():
                if hint.email_domain in (domains or []):
                    return _tenant(slug)
    return None


def _tenant(slug: str) -> TenantSignIn:
    return TenantSignIn(org=slug, start_path=f"/api/v1/saml/{slug}/login")
