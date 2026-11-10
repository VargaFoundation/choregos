# SPDX-License-Identifier: Apache-2.0
"""Conversation actions: `conversation_actions` (S63-10, ADR 0139 point 5)

A write an assistant proposed in a conversation, until its owner confirms or refuses it. Under forced RLS
on the organisation, and on PostgreSQL a RESTRICTIVE policy lets only the platform scope and the person it
belongs to read it. The downgrade drops the table and loses the proposals and what became of them.

Revision ID: e0d2f4a6c8e0
Revises: d9b1d3f5a7c9
Create Date: 2026-11-10 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "e0d2f4a6c8e0"
down_revision: str | None = "d9b1d3f5a7c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "conversation_actions"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=200), nullable=False),
        sa.Column("need", sa.String(length=63), nullable=False),
        sa.Column("operation", sa.String(length=128), nullable=False),
        sa.Column("arguments", Json(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", Json(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=200), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_conversation_actions_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_sessions.id"],
            name=op.f("fk_conversation_actions_conversation_id_agent_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversation_actions")),
    )
    for column in ("org_id", "conversation_id", "user_id"):
        op.create_index(op.f(f"ix_conversation_actions_{column}"), TABLE, [column], unique=False)
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
        op.execute(
            f"""
            CREATE POLICY {TABLE}_owner ON {TABLE} AS RESTRICTIVE
            USING ('*' = ANY(choregos_current_orgs()) OR user_id = choregos_utilisateur_courant())
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_owner ON {TABLE}")
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    for column in ("user_id", "conversation_id", "org_id"):
        op.drop_index(op.f(f"ix_conversation_actions_{column}"), table_name=TABLE)
    op.drop_table(TABLE)
