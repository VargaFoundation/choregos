# SPDX-License-Identifier: Apache-2.0
"""Assistant installations: `assistant_installations`, `assistant_tool_bindings` (S63-02, ADR 0133)

Both under forced RLS on the organisation, like `machine_accounts`. The downgrade drops both tables and
loses which assistants were installed and how their needs were bound; the plugins and connectors stay.

Revision ID: c2a4e6b8d0f1
Revises: b1d3f5a7c9e2
Create Date: 2026-11-10 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "c2a4e6b8d0f1"
down_revision: str | None = "b1d3f5a7c9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("assistant_installations", "assistant_tool_bindings")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _stamps() -> list[sa.Column[Any]]:
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "assistant_installations",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("plugin", sa.String(length=64), nullable=False),
        sa.Column("plugin_version", sa.String(length=128), nullable=False),
        sa.Column("manifest_digest", sa.String(length=80), nullable=False),
        sa.Column("assistant_name", sa.String(length=63), nullable=False),
        sa.Column("config", Json(), nullable=False),
        sa.Column("owner_groups", Json(), nullable=False),
        sa.Column("audience_groups", Json(), nullable=False),
        sa.Column("channel", sa.String(length=16), server_default="stable", nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("policy_overrides", Json(), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        *_stamps(),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_assistant_installations_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_installations")),
        sa.UniqueConstraint("org_id", "slug", name="uq_assistant_installations_org_slug"),
    )
    op.create_index(
        op.f("ix_assistant_installations_org_id"), "assistant_installations", ["org_id"], unique=False
    )
    op.create_table(
        "assistant_tool_bindings",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("need", sa.String(length=63), nullable=False),
        sa.Column("connector_id", sa.String(length=36), nullable=False),
        sa.Column("operations", Json(), nullable=False),
        sa.Column("identity", sa.String(length=16), nullable=False),
        sa.Column("approval", sa.String(length=16), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        *_stamps(),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_assistant_tool_bindings_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["assistant_installations.id"],
            name=op.f("fk_assistant_tool_bindings_installation_id_assistant_installations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["org_connectors.id"],
            name=op.f("fk_assistant_tool_bindings_connector_id_org_connectors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_tool_bindings")),
        sa.UniqueConstraint("installation_id", "need", name="uq_assistant_tool_bindings_need"),
    )
    for column in ("org_id", "installation_id", "connector_id"):
        op.create_index(
            op.f(f"ix_assistant_tool_bindings_{column}"), "assistant_tool_bindings", [column], unique=False
        )

    if _postgres():
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"""
                CREATE POLICY {table}_org_isolation ON {table}
                USING (
                  '*' = ANY(choregos_current_orgs())
                  OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
                )
                """
            )


def downgrade() -> None:
    if _postgres():
        for table in reversed(TABLES):
            op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    for column in ("connector_id", "installation_id", "org_id"):
        op.drop_index(op.f(f"ix_assistant_tool_bindings_{column}"), table_name="assistant_tool_bindings")
    op.drop_table("assistant_tool_bindings")
    op.drop_index(op.f("ix_assistant_installations_org_id"), table_name="assistant_installations")
    op.drop_table("assistant_installations")
