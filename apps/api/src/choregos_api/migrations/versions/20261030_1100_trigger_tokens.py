# SPDX-License-Identifier: Apache-2.0
"""The tokens of projects' HTTP triggers: `trigger_tokens` (S51-01, ADR 0113)

One row per trigger that has a token: its SHA-256 only, under forced RLS like `received_events`.

Revision ID: f3b5d7e9a1c2
Revises: b4d6f8a0c2e7
Create Date: 2026-10-30 11:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b5d7e9a1c2"
down_revision: str | None = "b4d6f8a0c2e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "trigger_tokens",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=63), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_trigger_tokens_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_trigger_tokens_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trigger_tokens")),
        sa.UniqueConstraint("project_id", "name", name="uq_trigger_tokens_project_name"),
    )
    op.create_index(op.f("ix_trigger_tokens_org_id"), "trigger_tokens", ["org_id"], unique=False)
    op.create_index(op.f("ix_trigger_tokens_project_id"), "trigger_tokens", ["project_id"], unique=False)

    if _postgres():
        op.execute("ALTER TABLE trigger_tokens ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE trigger_tokens FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY trigger_tokens_org_isolation ON trigger_tokens
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS trigger_tokens_org_isolation ON trigger_tokens")
    op.drop_index(op.f("ix_trigger_tokens_project_id"), table_name="trigger_tokens")
    op.drop_index(op.f("ix_trigger_tokens_org_id"), table_name="trigger_tokens")
    op.drop_table("trigger_tokens")
