# SPDX-License-Identifier: Apache-2.0
"""The reads journal of an assistant: `assistant_reads` (S63-16, ADR 0133 point 6)

One line per tool call of an assistant's conversation (installation, user, conversation, need, operation,
subject, delegation, moment; never the content read), under forced RLS on the organisation. The downgrade
drops the table and loses the journal.

Revision ID: e1b3d5f7a9c1
Revises: d9b1d3f5a7c9
Create Date: 2026-11-10 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1b3d5f7a9c1"
down_revision: str | None = "d9b1d3f5a7c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "assistant_reads"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=200), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("work_item_key", sa.String(length=200), nullable=True),
        sa.Column("need", sa.String(length=63), nullable=False),
        sa.Column("operation", sa.String(length=200), nullable=False),
        sa.Column("subject_type", sa.String(length=63), nullable=True),
        sa.Column("subject_key", sa.String(length=300), nullable=True),
        sa.Column("delegation_id", sa.String(length=36), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_assistant_reads_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["assistant_installations.id"],
            name=op.f("fk_assistant_reads_installation_id_assistant_installations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_reads")),
    )
    for column in ("org_id", "installation_id", "user_id"):
        op.create_index(op.f(f"ix_assistant_reads_{column}"), TABLE, [column], unique=False)
    op.create_index("ix_assistant_reads_installation_at", TABLE, ["installation_id", "at"], unique=False)
    op.create_index(
        "ix_assistant_reads_subject", TABLE, ["installation_id", "subject_type", "subject_key"], unique=False
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
    op.drop_index("ix_assistant_reads_subject", table_name=TABLE)
    op.drop_index("ix_assistant_reads_installation_at", table_name=TABLE)
    for column in ("user_id", "installation_id", "org_id"):
        op.drop_index(op.f(f"ix_assistant_reads_{column}"), table_name=TABLE)
    op.drop_table(TABLE)
