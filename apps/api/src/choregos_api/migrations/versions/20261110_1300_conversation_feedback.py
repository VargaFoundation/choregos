# SPDX-License-Identifier: Apache-2.0
"""Feedback on a conversation's answers: `conversation_feedback` (S63-17, ADR 0140)

A thumb up or down, with an optional comment, on one answer of an agent session, kept with the agent
revision that gave it. A thumb down also holds a candidate eval case (pseudonymised, `proposed` to the
owners, in no suite until one of them adds it). The table is under forced organisation RLS, like the
session it hangs from.

Revision ID: c1e3a5c7e9b2
Revises: c4b6d8f0a2e3
Create Date: 2026-11-10 13:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1e3a5c7e9b2"
down_revision: str | None = "c4b6d8f0a2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "conversation_feedback"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=200), nullable=False),
        sa.Column("agent_slug", sa.String(length=64), nullable=False),
        sa.Column("agent_version", sa.Integer(), nullable=False),
        sa.Column("response_seq", sa.Integer(), nullable=False),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("candidate", sa.JSON(), nullable=True),
        sa.Column("candidate_status", sa.String(length=16), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        *(
            sa.Column(
                name,
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            )
            for name in ("created_at", "updated_at")
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_conversation_feedback_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            name=op.f("fk_conversation_feedback_session_id_agent_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_feedback")),
        sa.UniqueConstraint("session_id", "response_seq", name="uq_conversation_feedback_answer"),
    )
    for column in ("org_id", "session_id", "user_id", "agent_slug"):
        op.create_index(op.f(f"ix_conversation_feedback_{column}"), TABLE, [column], unique=False)
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
    for column in ("agent_slug", "user_id", "session_id", "org_id"):
        op.drop_index(op.f(f"ix_conversation_feedback_{column}"), table_name=TABLE)
    op.drop_table(TABLE)
