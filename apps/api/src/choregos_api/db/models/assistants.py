# SPDX-License-Identifier: Apache-2.0
"""Assistant installations and the bindings of their needs (ADR 0133, S63-02)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class AssistantInstallation(Base, PkMixin, TimestampMixin):
    """An assistant of an installed plugin, installed in an organisation for an audience.

    The manifest is never copied: it is read again from the installed plugin (`plugin`, at the digest in
    `manifest_digest`), so an upgrade of the plugin is seen at once. `status` is `incomplete` while the
    readiness has an item, `ready` when it has none, `suspended` once the plugin is uninstalled or while a
    person has stopped it (`stopped_at`, ADR 0142 §4 bis), whatever the rest says.
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
    #: The generation of the task webhook's key (`task_delivery.webhook_secret`) and when an owner was last
    #: shown it: a key is shown once, a rotation makes a new generation.
    webhook_secret_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    webhook_secret_shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Who stopped the assistant, when and why (ADR 0142 §4 bis): set, its status reads `suspended` until
    #: a resume clears it, whatever the readiness or the plugin say.
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    stopped_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


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


class AssistantKnowledgeBinding(Base, PkMixin, TimestampMixin):
    """A knowledge need of an installation (`knowledge[].bind`), bound to one existing base of the
    organisation, by reference (ADR 0159): one row per base. Nothing of the base is copied or changed; a
    conversation reads it only at a turn whose person may read the base (ADR 0081)."""

    __tablename__ = "assistant_knowledge_bindings"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "name", "knowledge_base_id", name="uq_assistant_knowledge_bindings_base"
        ),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    installation_id: Mapped[str] = mapped_column(
        ForeignKey("assistant_installations.id", ondelete="CASCADE"), index=True
    )
    #: The knowledge need's name in the manifest.
    name: Mapped[str] = mapped_column(String(63))
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    #: False once a plugin upgrade dropped the need (S63-22): kept for the audit trail, never served.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    bound_by: Mapped[str | None] = mapped_column(String(320), nullable=True)


class ConversationAction(Base, PkMixin, TimestampMixin):
    """A write the assistant proposed in a conversation and the person has not yet decided (ADR 0139 point 5).

    The call of an `inline` write need does not reach the source: it lands here as `proposed`, with the
    arguments as the agent gave them and a readable `summary`. The conversation's owner confirms it (the
    broker executes it with THEIR identity, `executed` with the `result`) or refuses it; one nobody decided
    in a day is `expired`. `confirmed` without `executed_at` is a confirmation the source answered with an
    error: the person may confirm again."""

    __tablename__ = "conversation_actions"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True
    )
    #: The conversation's owner: the only one who sees and decides it.
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    need: Mapped[str] = mapped_column(String(63))
    operation: Mapped[str] = mapped_column(String(128))
    arguments: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    summary: Mapped[str] = mapped_column(Text)
    #: `proposed`, `confirmed`, `executed`, `refused`, `expired`, or `governed`: a `console` approval, decided
    #: as a governed action (ADR 0035) whose id and decision path are in `result`.
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    result: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: What the source's preview of the write returned at the proposal, when it serves one (S63-10).
    diff: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: `write` (a write need's operation) or `launch` (the start of a project's workflow, ADR 0158).
    kind: Mapped[str] = mapped_column(String(16), default="write", server_default="write")
    #: For a launch, the binding when it was proposed: `{project_id, project, workflow}`.
    target: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)


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
    #: Digest of what the person consented to (the source's authorization server, client and address): the
    #: account is not used, and nothing is sent to a new address, once an editor changed any of them.
    provider_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
