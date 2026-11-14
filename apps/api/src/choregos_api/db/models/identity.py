# SPDX-License-Identifier: Apache-2.0
"""Identity: organisations, users, memberships, API tokens, browser sessions and linked forge accounts."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, Json, PkMixin, TimestampMixin, utcnow

if TYPE_CHECKING:
    from .projects import Project


class Organization(Base, PkMixin, TimestampMixin):
    __tablename__ = "organizations"

    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    settings: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)

    projects: Mapped[list[Project]] = relationship(back_populates="org", cascade="all, delete-orphan")


class OrgSlugAlias(Base):
    """A slug an organisation had (ADR 0090). The organisation's id is its identity; its slug is an
    address, and an old address keeps resolving to it. It maps an address to an id and holds no content:
    outside RLS, since an address is resolved before any scope exists.
    The alias is unique across every alias, and never equal to a current slug (checked on write)."""

    __tablename__ = "org_slug_aliases"

    alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class User(Base, PkMixin, TimestampMixin):
    __tablename__ = "users"

    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_service: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The identity provider's groups, as of the last sign-in through it — what an approval rule
    #: that names a group checks (ADR 0052). The IdP is authoritative: each sign-in replaces them.
    groups: Mapped[list[str]] = mapped_column(Json, default=list)
    #: A session cookie without a session id (opened before ADR 0130) issued before this instant is
    #: refused: "sign out my other sessions" reaches the cookies no row records.
    sessions_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Membership(Base, PkMixin, TimestampMixin):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id", "project_id", name="user_org_project"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    #: `idp` when an IdP sign-in created the row from the provider's groups, `manual` otherwise
    #: (administrator, bootstrap, project creator); a manual row is never revoked by a sign-in.
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")

    user: Mapped[User] = relationship(lazy="joined")


class MachineAccount(Base, PkMixin, TimestampMixin):
    """An application that calls under its own name, on one project (ADR 0131)."""

    __tablename__ = "machine_accounts"
    __table_args__ = (UniqueConstraint("project_id", "slug", name="uq_machine_accounts_project_slug"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(63))
    display_name: Mapped[str] = mapped_column(String(200), default="")
    writes_per_day: Mapped[int] = mapped_column(Integer, default=500)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    #: Set when the account ends: the row stays so that the journal still reads, its slug stays taken.
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrgGroup(Base, PkMixin, TimestampMixin):
    """A group of people that belongs to an organisation (ADR 0160), with exactly one source.

    `internal`: created and edited in the console by an organisation administrator. `scim`: pushed by
    an identity provider (`external_id` is its identifier there), read-only through the core API. The
    identity provider's `groups` claim (`User.groups`) stays a third source, read as before."""

    __tablename__ = "org_groups"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_org_groups_org_slug"),
        Index(
            "uq_org_groups_org_external_id",
            "org_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(63))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    #: `internal` or `scim`: who may change the group (ADR 0160).
    source: Mapped[str] = mapped_column(String(16), default="internal")
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class OrgGroupMember(Base, PkMixin):
    """One person in one organisation group (ADR 0160)."""

    __tablename__ = "org_group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_org_group_members_group_user"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("org_groups.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    added_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )


class ApiToken(Base, PkMixin, TimestampMixin):
    __tablename__ = "api_tokens"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    scopes: Mapped[list[str]] = mapped_column(Json, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: An MCP token can be bound to a project: it then sees only that one (ADR 0030).
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: The client of the last call (`User-Agent`, truncated): the Integrations page says "connected".
    last_client: Mapped[str | None] = mapped_column(String(200), nullable=True)


class UserSession(Base, PkMixin, TimestampMixin):
    """A browser session (ADR 0130): what the signed cookie's `sid` names.

    The cookie stays the proof (signed, bounded by `exp`); the row says whether it still holds, and
    what its owner sees on My account: when it opened, its last activity, the browser and address of
    its last request. Revoking sets `revoked_at`; the row is kept until it expires, for the list."""

    __tablename__ = "user_sessions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: The sign-in channel: `oidc`, `saml`, `dev`... as `ouvrir_la_session` was told.
    channel: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The `User-Agent` and client address of the last request, truncated.
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LinkedAccount(Base, PkMixin, TimestampMixin):
    """A member's account on a code forge (ADR 0161; before it, `tracker_identities`, ADR 0102).

    One per member and forge instance (`provider` + `base_url`), one member per account on an instance.
    `verified`: the member proved it by signing in to the forge (OAuth); otherwise an organisation
    administrator declared it. Only `external_id`, the forge's stable numeric user id, is ever matched:
    a login, an avatar or a profile URL is shown, nothing more."""

    __tablename__ = "linked_accounts"
    __table_args__ = (
        UniqueConstraint("org_id", "provider", "base_url", "external_id", name="uq_linked_accounts_external"),
        UniqueConstraint("org_id", "user_id", "provider", "base_url", name="uq_linked_accounts_member"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: `github`, `gitlab` or `forgejo`.
    provider: Mapped[str] = mapped_column(String(16))
    #: The instance, normalised: `https://github.com`, `https://gitlab.example`.
    base_url: Mapped[str] = mapped_column(String(255))
    external_id: Mapped[str] = mapped_column(String(64))
    login: Mapped[str] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    profile_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The organisation connector whose OAuth application proved it; `NULL` for a declared link.
    connector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Who wrote the link: the member themself (verified) or the administrator who declared it.
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
