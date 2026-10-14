# SPDX-License-Identifier: Apache-2.0
"""Knowledge index (S33-03, ADR 0051): `knowledge_chunks`, full-text and, when it can, pgvector

On PostgreSQL the chunks get a generated `search_tsv` (configuration `simple`: no stemming, any
language) under a GIN index. The `vector` extension is created if the migrating role may (it is not
a trusted extension: a superuser, or the cluster's bootstrap, must have created it otherwise); only
when it exists is `embedding_vec` added. Without it, the index serves full-text only, and says so.

Revision ID: e6a8b0c2d4f5
Revises: a9c1e3f5b7d9
Create Date: 2026-10-12 13:00:00+00:00
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "e6a8b0c2d4f5"
down_revision: str | None = "a9c1e3f5b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "knowledge_chunks"
logger = logging.getLogger("alembic.knowledge")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _vector_available() -> bool:
    """Creates the extension if this role may; says whether it is there."""
    bind = op.get_bind()
    try:
        with bind.begin_nested():
            bind.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    except sa.exc.DBAPIError as exc:
        logger.warning("pgvector unavailable (%s): the knowledge index serves full-text only", exc.orig)
    return bool(bind.exec_driver_sql("SELECT 1 FROM pg_extension WHERE extname = 'vector'").scalar())


def upgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(sa.Column("eval_set", Json(), nullable=False, server_default=sa.text("'[]'")))
        batch.add_column(sa.Column("eval_result", Json(), nullable=True))
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("knowledge_base_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading_path", Json(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Json(), nullable=True),
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
            ["org_id"], ["organizations.id"], name=op.f("fk_knowledge_chunks_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"], ["knowledge_bases.id"],
            name=op.f("fk_knowledge_chunks_knowledge_base_id_knowledge_bases"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"],
            name=op.f("fk_knowledge_chunks_document_id_knowledge_documents"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_knowledge_chunks")),
    )
    op.create_index(op.f("ix_knowledge_chunks_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_knowledge_chunks_knowledge_base_id"), TABLE, ["knowledge_base_id"], unique=False)
    op.create_index(op.f("ix_knowledge_chunks_document_id"), TABLE, ["document_id"], unique=False)

    if _postgres():
        op.execute(
            f"ALTER TABLE {TABLE} ADD COLUMN search_tsv tsvector "
            "GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED"
        )
        op.execute(f"CREATE INDEX ix_{TABLE}_search_tsv ON {TABLE} USING gin (search_tsv)")
        if _vector_available():
            # No fixed dimension: the embedding model is the deployment's choice. Exact search per
            # knowledge base; an HNSW index needs a fixed dimension and is a later step.
            op.execute(f"ALTER TABLE {TABLE} ADD COLUMN embedding_vec vector")
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
    op.drop_index(op.f("ix_knowledge_chunks_document_id"), table_name=TABLE)
    op.drop_index(op.f("ix_knowledge_chunks_knowledge_base_id"), table_name=TABLE)
    op.drop_index(op.f("ix_knowledge_chunks_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_column("eval_result")
        batch.drop_column("eval_set")
