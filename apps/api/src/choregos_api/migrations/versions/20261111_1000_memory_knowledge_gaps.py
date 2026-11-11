# SPDX-License-Identifier: Apache-2.0
"""Seed documents an owner deleted are remembered: `knowledge_seed_tombstones` (S63-06, ADR 0138)

A plugin upgrade does not bring back a seed document the owners deleted. The table is under forced RLS on
the organisation. The downgrade drops it: a `follow` upgrade would add the deleted documents back.

Revision ID: a2e2c0f0e0d2
Revises: a5e5c0f0e0d5
Create Date: 2026-11-11 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2e2c0f0e0d2"
down_revision: str | None = "a5e5c0f0e0d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "knowledge_seed_tombstones"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("deleted_by", sa.String(length=320), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_knowledge_seed_tombstones_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_knowledge_seed_tombstones_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_seed_tombstones")),
        sa.UniqueConstraint("knowledge_base_id", "path", name="uq_knowledge_seed_tombstones_base_path"),
    )
    for column in ("org_id", "knowledge_base_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column], unique=False)
    if op.get_bind().dialect.name == "postgresql":
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
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    for column in ("knowledge_base_id", "org_id"):
        op.drop_index(op.f(f"ix_{TABLE}_{column}"), table_name=TABLE)
    op.drop_table(TABLE)
