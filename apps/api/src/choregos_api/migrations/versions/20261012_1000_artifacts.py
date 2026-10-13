# SPDX-License-Identifier: Apache-2.0
"""Typed, stored, traced artifacts (S31-01, ADR 0049): `artifacts`

One row per revision of a named output of a work item; never modified. Under forced RLS, like the
rest.

Revision ID: b4d6f8a0c2e4
Revises: a3c5e7f9b1d2
Create Date: 2026-10-12 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "b4d6f8a0c2e4"
down_revision: str | None = "a3c5e7f9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "artifacts"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("media_type", sa.String(length=200), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("content", Json(), nullable=True),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("storage_url", sa.String(length=700), nullable=True),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("agent_slug", sa.String(length=64), nullable=True),
        sa.Column("agent_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_artifacts_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_artifacts_work_item_id_work_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_artifacts")),
        sa.UniqueConstraint("work_item_id", "name", "version", name="uq_artifacts_version"),
    )
    op.create_index(op.f("ix_artifacts_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_artifacts_work_item_id"), TABLE, ["work_item_id"], unique=False)

    if _postgres():
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {TABLE}_org_isolation ON {TABLE}
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    op.drop_index(op.f("ix_artifacts_work_item_id"), table_name=TABLE)
    op.drop_index(op.f("ix_artifacts_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
