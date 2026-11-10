# SPDX-License-Identifier: Apache-2.0
"""Offered knowledge: `knowledge_documents.origin` and `seed_digest`, `knowledge_update_proposals` (S63-06, ADR 0138)

Every existing document is `local` (nothing came from a seed). The proposals table is under forced RLS on
the organisation. The downgrade drops the table and the two columns: which documents came from a seed, and
the pending proposals, are lost; the documents stay.

Revision ID: e6a8c0e2a4b6
Revises: d9b1d3f5a7c9
Create Date: 2026-11-10 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6a8c0e2a4b6"
down_revision: str | None = "d9b1d3f5a7c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "knowledge_update_proposals"


def upgrade() -> None:
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.add_column(sa.Column("origin", sa.String(length=200), server_default="local", nullable=False))
        batch.add_column(sa.Column("seed_digest", sa.String(length=80), nullable=True))
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("proposed_content", sa.Text(), nullable=True),
        sa.Column("seed_digest", sa.String(length=80), nullable=True),
        sa.Column("plugin_version", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=320), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_knowledge_update_proposals_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["assistant_installations.id"],
            name=op.f("fk_knowledge_update_proposals_installation_id_assistant_installations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_knowledge_update_proposals_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_update_proposals")),
        sa.CheckConstraint("kind IN ('add', 'update', 'remove')", name="ck_knowledge_update_proposals_kind"),
        sa.CheckConstraint(
            "status IN ('proposed', 'accepted', 'merged', 'refused')",
            name="ck_knowledge_update_proposals_status",
        ),
    )
    for column in ("org_id", "installation_id", "knowledge_base_id"):
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
    for column in ("knowledge_base_id", "installation_id", "org_id"):
        op.drop_index(op.f(f"ix_{TABLE}_{column}"), table_name=TABLE)
    op.drop_table(TABLE)
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.drop_column("seed_digest")
        batch.drop_column("origin")
