# SPDX-License-Identifier: Apache-2.0
"""The SAML identity provider of an organisation (ADR 0170), as the platform administrator sets it."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SamlProviderIn(BaseModel):
    idp_entity_id: str = Field(min_length=3, max_length=500)
    sso_url: str = Field(pattern=r"^https://", max_length=1000)
    #: The provider's signing certificate (PEM, X.509). Pinned: assertions are verified against this one.
    certificate_pem: str = Field(min_length=100, max_length=20000)
    #: The attribute carrying the e-mail; the `NameID` when absent.
    email_attribute: str | None = Field(default=None, max_length=200)
    groups_attribute: str | None = Field(default=None, max_length=200)
    #: A group value at the provider -> a core group (`org-admins`, `developers`...). Others are dropped.
    group_map: dict[str, str] = Field(default_factory=dict)
    #: E-mail domains this provider may vouch for. One domain belongs to one organisation.
    domains: list[str] = Field(min_length=1, max_length=50)


class SamlProvider(BaseModel):
    """What `PUT` takes, as it is stored (empty while no provider is configured), and what the provider
    must know of us."""

    idp_entity_id: str | None = None
    sso_url: str | None = None
    certificate_pem: str | None = None
    email_attribute: str | None = None
    groups_attribute: str | None = None
    group_map: dict[str, str] = Field(default_factory=dict)
    domains: list[str] = Field(default_factory=list)
    sp_entity_id: str
    acs_url: str
