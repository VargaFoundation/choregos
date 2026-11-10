"""Knowledge bases of an assistant installation: `knowledge_bases.installation_id` (S63-05, ADR 0138)

The one place that says which bases an installation offers its conversations: the base names its
installation. Null for every existing base. The downgrade drops the column and loses the link, not the bases.

Revision ID: d5a7c9e1f3b5
Revises: c4b6d8f0a2e3
Create Date: 2026-11-10 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a7c9e1f3b5"
down_revision: str | None = "c4b6d8f0a2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(sa.Column("installation_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_knowledge_bases_installation_id_assistant_installations",
            "assistant_installations",
            ["installation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_knowledge_bases_installation_id", ["installation_id"])


def downgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_index("ix_knowledge_bases_installation_id")
        batch.drop_constraint("fk_knowledge_bases_installation_id_assistant_installations", type_="foreignkey")
        batch.drop_column("installation_id")
