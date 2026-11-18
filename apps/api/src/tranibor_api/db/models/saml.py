# SPDX-License-Identifier: Apache-2.0
"""SAML sign-in (ADR 0170): one identity provider per organisation, and the assertions already consumed."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class OrgSamlIdp(Base, PkMixin, TimestampMixin):
    """The SAML identity provider of ONE organisation.

    `certificate_pem` is the provider's signing certificate, pinned: an assertion is verified against it
    and never against a certificate the message carries. `group_map` translates a group value at the
    provider into a core group (`org-admins`, `developers`...); a value it does not list is dropped.
    `domains` are the e-mail domains the provider may vouch for, declared by a platform administrator."""

    __tablename__ = "org_saml_idps"
    __table_args__ = (UniqueConstraint("org_id", name="uq_org_saml_idps_org_id"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    idp_entity_id: Mapped[str] = mapped_column(String(500))
    sso_url: Mapped[str] = mapped_column(String(1000))
    certificate_pem: Mapped[str] = mapped_column(Text)
    email_attribute: Mapped[str | None] = mapped_column(String(200), nullable=True)
    groups_attribute: Mapped[str | None] = mapped_column(String(200), nullable=True)
    group_map: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    domains: Mapped[list[str]] = mapped_column(Json, default=list)


class SamlConsumedAssertion(Base):
    """An assertion id is good once: it is kept until the assertion would have expired anyway.

    Not organisation data (the id is the provider's): read and written in the platform scope by the
    assertion consumer service."""

    __tablename__ = "saml_consumed_assertions"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
