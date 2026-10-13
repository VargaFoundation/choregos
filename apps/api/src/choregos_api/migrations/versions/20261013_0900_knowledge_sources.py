# SPDX-License-Identifier: Apache-2.0
"""Knowledge sources (S33-04, ADR 0051): `knowledge_sources`, and what a document keeps of its source

Under forced RLS, like the rest. A document synced from a source names it (`source_id`, cascade:
a source removed takes its documents with it), with its identity and version there.

Revision ID: f7b9d1e3a5c6
Revises: e6a8b0c2d4f5
Create Date: 2026-10-13 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "f7b9d1e3a5c6"
down_revision: str | None = "e6a8b0c2d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "knowledge_sources"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=True),
        sa.Column("config", Json(), nullable=False),
        sa.Column("cursor", Json(), nullable=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_summary", Json(), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_knowledge_sources_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"],
            name=op.f("fk_knowledge_sources_knowledge_base_id_knowledge_bases"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_sources")),
    )
    op.create_index(op.f("ix_knowledge_sources_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_knowledge_sources_knowledge_base_id"), TABLE, ["knowledge_base_id"], unique=False)
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.add_column(sa.Column("source_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("source_ref", sa.String(length=1000), nullable=True))
        batch.add_column(sa.Column("source_version", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("source_url", sa.String(length=2000), nullable=True))
        batch.create_foreign_key(
            op.f("fk_knowledge_documents_source_id_knowledge_sources"),
            TABLE,
            ["source_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index(op.f("ix_knowledge_documents_source_id"), ["source_id"], unique=False)

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
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.drop_index(op.f("ix_knowledge_documents_source_id"))
        batch.drop_constraint(op.f("fk_knowledge_documents_source_id_knowledge_sources"), type_="foreignkey")
        batch.drop_column("source_url")
        batch.drop_column("source_version")
        batch.drop_column("source_ref")
        batch.drop_column("source_id")
    op.drop_index(op.f("ix_knowledge_sources_knowledge_base_id"), table_name=TABLE)
    op.drop_index(op.f("ix_knowledge_sources_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
