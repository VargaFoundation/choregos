# SPDX-License-Identifier: Apache-2.0
"""Assistant installations and the bindings of their needs (ADR 0133, S63-02)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class AssistantInstallation(Base, PkMixin, TimestampMixin):
    """An assistant of an installed plugin, installed in an organisation for an audience.

    The manifest is never copied: it is read again from the installed plugin (`plugin`, at the digest in
    `manifest_digest`), so an upgrade of the plugin is seen at once. `status` is `incomplete` while the
    readiness has an item, `ready` when it has none, `suspended` once the plugin is uninstalled.
    """

    __tablename__ = "assistant_installations"
    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_assistant_installations_org_slug"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(63))
    plugin: Mapped[str] = mapped_column(String(64))
    plugin_version: Mapped[str] = mapped_column(String(128))
    manifest_digest: Mapped[str] = mapped_column(String(80))
    assistant_name: Mapped[str] = mapped_column(String(63))
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: IdP groups that configure and bind the installation (besides the organisation's administrators).
    owner_groups: Mapped[list[str]] = mapped_column(Json, default=list)
    #: IdP groups that may talk to it.
    audience_groups: Mapped[list[str]] = mapped_column(Json, default=list)
    #: The channel of the agent the installation follows: `stable` or `candidate` (ADR 0071).
    channel: Mapped[str] = mapped_column(String(16), default="stable", server_default="stable")
    status: Mapped[str] = mapped_column(String(16), default="incomplete")
    #: What the owners narrow of the manifest's policy (budgets, retention).
    policy_overrides: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class AssistantToolBinding(Base, PkMixin, TimestampMixin):
    """A need of an installation, bound to a connector of the organisation."""

    __tablename__ = "assistant_tool_bindings"
    __table_args__ = (UniqueConstraint("installation_id", "need", name="uq_assistant_tool_bindings_need"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_installations.id", ondelete="CASCADE"), index=True
    )
    need: Mapped[str] = mapped_column(String(63))
    connector_id: Mapped[str] = mapped_column(ForeignKey("org_connectors.id", ondelete="CASCADE"), index=True)
    #: The manifest's operations of the need, as bound.
    operations: Mapped[list[str]] = mapped_column(Json, default=list)
    identity: Mapped[str] = mapped_column(String(16))
    approval: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: False: an optional need the owners switched off.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())


class ConnectedAccount(Base, PkMixin, TimestampMixin):
    """A person's account at a source that answers in their name (`user_consent`, ADR 0135 point 1).

    The refresh token is never a column: `secret_ref` names its sealed secret (ADR 0058). Under forced RLS
    on the organisation and a RESTRICTIVE policy that lets only the owner (and the platform scope) in."""

    __tablename__ = "connected_accounts"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "connector", name="uq_connected_accounts_member"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    #: The organisation connector's name.
    connector: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16), default="user_consent", server_default="user_consent")
    #: `store:<name>` of the sealed refresh token.
    secret_ref: Mapped[str] = mapped_column(String(300))
    scopes: Mapped[list[str]] = mapped_column(Json, default=list)
    #: The person's subject at the source, when the token response says it.
    subject: Mapped[str | None] = mapped_column(String(320), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: `connected`, or `reconnect` once the source refused the refresh token.
    status: Mapped[str] = mapped_column(String(16), default="connected", server_default="connected")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
