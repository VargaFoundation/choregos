# SPDX-License-Identifier: Apache-2.0
"""Organisation connectors, operations and policies, GitHub installations, egress rules."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class OrgConnector(Base, PkMixin, TimestampMixin):
    """A connector instance of the ORGANISATION (ADR 0034): an Entra directory, an MCP server,
    a device manager. Declared once by the administrator, named, and each of its
    operations carries its policy. Projects use it; they do not declare it."""

    __tablename__ = "org_connectors"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_org_connectors_org_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    type: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: REFERENCES (`env:NAME`), never values.
    secret_refs: Mapped[dict[str, str] | None] = mapped_column(Json, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: `<id>@<catalogue_version>` when installed from the MCP catalogue (ADR 0065); `NULL` otherwise.
    catalogue_entry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: `all_projects` (before ADR 0074: every project whose groups match) or `selected_projects`
    #: (only the projects with a row in `org_connector_projects`).
    availability: Mapped[str] = mapped_column(
        String(24), default="all_projects", server_default="all_projects"
    )
    #: The server a plugin brings, when the connector is one (ADR 0096): its declaration as the
    #: installer wrote it (image, port, paths, `config_schema`, `secret_fields`, `egress`, the declared
    #: `operations`, the Service URL). Written by the installer only, never by the connector routes:
    #: the broker reads its operations as the governance floor. `NULL` for any other connector.
    plugin_server: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: The outpost an `mcp-outpost` connector routes to (ADR 0149 §1): how the platform's own paths
    #: learn which outpost to name before they touch its rows (ADR 0148 §6), and why an outpost that
    #: still has connectors is not deleted. `NULL` for any other connector.
    outpost_id: Mapped[str | None] = mapped_column(
        ForeignKey("outposts.id", ondelete="RESTRICT"), nullable=True, index=True
    )


class OrgConnectorProject(Base, PkMixin, TimestampMixin):
    """A project an organisation connector is turned on for (ADR 0074): the project's own lock,
    read only when the connector's availability is `selected_projects`."""

    __tablename__ = "org_connector_projects"
    __table_args__ = (UniqueConstraint("connector_id", "project_id", name="uq_org_connector_projects"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("org_connectors.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    enabled_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class ConnectorOperation(Base, PkMixin, TimestampMixin):
    """An operation of an instance, and its policy in the organisation: `allowed`, `approval`
    (a governed action, ADR 0035) or `forbidden`; the project groups entitled to it
    (none: all); its price; the digest of its schema (a drift closes it again, S19-03)."""

    __tablename__ = "connector_operations"
    __table_args__ = (
        UniqueConstraint("connector_id", "name", name="uq_connector_operations_connector_name"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    connector_id: Mapped[str] = mapped_column(ForeignKey("org_connectors.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    access: Mapped[str] = mapped_column(String(8), default="read")
    policy: Mapped[str] = mapped_column(String(16), default="forbidden")
    groups: Mapped[list[str]] = mapped_column(Json, default=list)
    price_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    schema_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: What the broker announces to the agent, and checks before reaching the server.
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: The `outputSchema` the server declares: a successful result that does not match it is refused
    #: before the agent reads it (#755).
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The identity-provider group whose members approve the actions it proposes (ADR 0052).
    approver_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The most one of its results may weigh when it reaches the agent; none: the platform's default
    #: (S40-17). Never above the platform's ceiling.
    result_max_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: A JSON Schema every call's arguments must also satisfy, whichever agent calls (S60-06, ADR 0128);
    #: `{project}` is the run's project slug. None: the operation's input schema alone.
    arguments: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)


class ProjectOperationPolicy(Base, PkMixin, TimestampMixin):
    """What a project NARROWS of an organisation operation, never what it widens."""

    __tablename__ = "project_operation_policies"
    __table_args__ = (UniqueConstraint("project_id", "operation_id", name="uq_project_operation_policies"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("connector_operations.id", ondelete="CASCADE"), index=True
    )
    policy: Mapped[str] = mapped_column(String(16))
    #: What the project narrows of the arguments, on top of the organisation's (S60-06, ADR 0128).
    arguments: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)


class GitHubInstallation(Base, TimestampMixin):
    """An installation of the deployment's GitHub App on a GitHub account (ADR 0073).

    The primary key is GitHub's installation id, which nobody types: it is discovered by polling
    `GET /app/installations`, by the Setup URL redirect after an install, and by webhooks. `org_id`
    is the Choregos organisation that CLAIMED it; a token for `owner/repo` is only minted through an
    installation claimed by the project's organisation. Unclaimed rows are visible to the platform
    scope only."""

    __tablename__ = "github_installations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    account_login: Mapped[str] = mapped_column(String(200))
    #: `account_login` lower-cased: GitHub logins are case-insensitive, the lookups use this one.
    account_key: Mapped[str] = mapped_column(String(200), index=True)
    account_id: Mapped[int] = mapped_column(BigInteger)
    #: `Organization` or `User`
    account_type: Mapped[str] = mapped_column(String(32))
    app_id: Mapped[str] = mapped_column(String(32), default="")
    #: `all` or `selected`
    repository_selection: Mapped[str] = mapped_column(String(16), default="selected")
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OrgEgressRule(Base, PkMixin, TimestampMixin):
    """One host an organisation lets the platform call for it (S39-22, ADR 0066).

    `status` is `requested` (a member asked, nobody decided), `allowed`, `refused` or `revoked`;
    only `allowed` opens anything. At most one live (`requested` or `allowed`) row per host."""

    __tablename__ = "org_egress_rules"
    __table_args__ = (
        Index(
            "uq_org_egress_rules_live_host",
            "org_id",
            "host",
            unique=True,
            postgresql_where=text("status IN ('requested', 'allowed')"),
            sqlite_where=text("status IN ('requested', 'allowed')"),
        ),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: exact, or `*.` as the leftmost label only; lowercased and IDNA-encoded
    host: Mapped[str] = mapped_column(String(255))
    ports: Mapped[list[int]] = mapped_column(Json, default=lambda: [443])
    #: the pod families that make the call: a subset of `api`, `workers`, `runners`
    used_by: Mapped[list[str]] = mapped_column(Json, default=lambda: ["api"])
    status: Mapped[str] = mapped_column(String(16), default="requested")
    reason: Mapped[str] = mapped_column(String(500), default="")
    #: `mcp-catalogue:<id>@<version>`, `org-connector:<name>`, `skill-import:<repo>`, `manual`, `migration`...
    origin: Mapped[str] = mapped_column(String(200), default="manual")
    requested_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
