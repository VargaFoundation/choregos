# SPDX-License-Identifier: Apache-2.0
"""Rights on knowledge bases (S42-11, ADR 0081): `knowledge_grants` and `knowledge_bases.restricted`

A grant gives a person, a group or a project a role on one base. Under forced RLS like every other
table of an organisation. No backfill: implicit owners stay computed from `owner_project`, so the
behaviour of every existing base is unchanged.

If the S42-10 migration (`knowledge_distillation_parts`) lands first, rechain `down_revision` after
it: there must stay one head.

Revision ID: e4a6c8d0b2f1
Revises: b3c5d7e9f1a2
Create Date: 2026-10-21 11:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4a6c8d0b2f1"
down_revision: str | None = "b3c5d7e9f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "knowledge_grants"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(
            sa.Column("restricted", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
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
            name=op.f("fk_knowledge_grants_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_knowledge_grants_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_grants")),
        sa.UniqueConstraint(
            "knowledge_base_id", "subject_type", "subject", name="uq_knowledge_grants_subject"
        ),
    )
    op.create_index(op.f("ix_knowledge_grants_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(
        op.f("ix_knowledge_grants_knowledge_base_id"), TABLE, ["knowledge_base_id"], unique=False
    )

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
    op.drop_index(op.f("ix_knowledge_grants_knowledge_base_id"), table_name=TABLE)
    op.drop_index(op.f("ix_knowledge_grants_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_column("restricted")
