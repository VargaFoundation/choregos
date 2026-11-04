# SPDX-License-Identifier: Apache-2.0
"""Organisation plugins (ADR 0093): the plugin, its files and its approvals."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin, utcnow


class OrgPlugin(Base, PkMixin, TimestampMixin):
    """A plugin installed in an organisation (ADR 0093, S43-04): which version, from which source, its
    content digest, and what it brought, by the identifier each item has in the organisation.

    `status`: `installed`, or `servers_pending` when the plugin declares servers the platform does not
    run yet (S43-06). `details` holds what the installer needs again: the template directories by name,
    the procedure files by id, the server documents, and what it skipped because the organisation
    already had an object of that name.
    """

    __tablename__ = "org_plugins"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_org_plugins_org_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(128))
    digest: Mapped[str] = mapped_column(String(80))
    manifest: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    contents: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    details: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="installed")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: Who installed this version; `None` for the start-up reconcile (S43-05).
    installed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrgPluginFile(Base, PkMixin, TimestampMixin):
    """A file of an installed plugin that the platform serves again later: its templates (with their
    files), its eval suites and its procedures. Keyed by the plugin's content digest, so an upgrade
    writes the new digest's files before it deletes the old ones; under RLS like the plugin row, so a
    private plugin's text is not readable from another organisation."""

    __tablename__ = "org_plugin_files"
    __table_args__ = (
        UniqueConstraint("org_id", "digest", "path", name="uq_org_plugin_files_org_digest_path"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    digest: Mapped[str] = mapped_column(String(80))
    path: Mapped[str] = mapped_column(String(500))
    content: Mapped[bytes] = mapped_column(LargeBinary)


class OrgPluginApproval(Base, PkMixin, TimestampMixin):
    """A plugin install or upgrade waiting for a second person (ADR 0093 §4, ADR 0050, S43-05).

    A plugin that brings a server is not installed on one administrator's word: the request is kept
    here, at the exact version and content digest that was checked, and nothing is written until an
    administrator other than `requested_by` approves it. `requested_by` is `None` when the start-up
    reconcile asked for a default plugin (`is_default`): any administrator of the organisation may then
    approve. `status`: `pending`, then `approved`.
    """

    __tablename__ = "org_plugin_approvals"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: `install` or `upgrade`
    action: Mapped[str] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(128))
    digest: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    requested_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    decided_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
