# SPDX-License-Identifier: Apache-2.0
"""Distillation in passes (S42-10c, ADR 0008 and 0082): `knowledge_distillation_parts`

One row per model call of a distillation, unique per (knowledge base, digest of what it read), so a
replayed activity or a repeated distillation does not pay for the same call twice. Under forced RLS
like every other table of an organisation. The downgrade drops the table: a distillation run after
it pays again for the calls it had kept.

Revision ID: b3c5d7e9f1a2
Revises: 3c5e7a9b1d2f
Create Date: 2026-10-21 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "b3c5d7e9f1a2"
down_revision: str | None = "3c5e7a9b1d2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "knowledge_distillation_parts"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("part_index", sa.Integer(), nullable=False),
        sa.Column("documents", Json(), nullable=False),
        sa.Column("characters", sa.Integer(), nullable=False),
        sa.Column("answer", Json(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f(f"fk_{TABLE}_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f(f"fk_{TABLE}_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint("knowledge_base_id", "digest", name="uq_knowledge_distillation_parts"),
    )
    op.create_index(op.f(f"ix_{TABLE}_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f(f"ix_{TABLE}_knowledge_base_id"), TABLE, ["knowledge_base_id"], unique=False)

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
    op.drop_index(op.f(f"ix_{TABLE}_knowledge_base_id"), table_name=TABLE)
    op.drop_index(op.f(f"ix_{TABLE}_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
