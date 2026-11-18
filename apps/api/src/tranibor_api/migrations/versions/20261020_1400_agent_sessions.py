# SPDX-License-Identifier: Apache-2.0
"""A person talking to an agent directly: `agent_sessions`, `agent_session_events` and
`cost_ledger.session_id` (S41-09, ADR 0080 §10)

A session is not a run: no work item, no workflow. Its spend is booked in the ledger like a run's,
with `session_id` instead of `run_id`. Both tables carry their organisation and sit under forced
RLS, like every organisation's table.

Revision ID: f7b9d1e3a5c8
Revises: e6a8c0f2b4d7
Create Date: 2026-10-20 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "f7b9d1e3a5c8"
down_revision: str | None = "e6a8c0f2b4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("agent_sessions", "agent_session_events")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            name,
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        )
        for name in ("created_at", "updated_at")
    ]


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("agent_slug", sa.String(length=64), nullable=False),
        sa.Column("agent_version", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("gateway_key_id", sa.String(length=128), nullable=True),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("responses", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_agent_sessions_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_agent_sessions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_sessions")),
    )
    for column in ("org_id", "project_id", "user_id"):
        op.create_index(op.f(f"ix_agent_sessions_{column}"), "agent_sessions", [column], unique=False)

    op.create_table(
        "agent_session_events",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_agent_session_events_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
            name=op.f("fk_agent_session_events_session_id_agent_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_session_events")),
        sa.UniqueConstraint("session_id", "seq", name="uq_agent_session_events_seq"),
    )
    for column in ("org_id", "session_id"):
        op.create_index(
            op.f(f"ix_agent_session_events_{column}"), "agent_session_events", [column], unique=False
        )

    # `batch_alter_table`: SQLite cannot add a foreign key afterwards.
    with op.batch_alter_table("cost_ledger") as batch:
        batch.add_column(sa.Column("session_id", sa.String(length=36), nullable=True))
        batch.create_index(op.f("ix_cost_ledger_session_id"), ["session_id"], unique=False)
        batch.create_foreign_key(
            op.f("fk_cost_ledger_session_id_agent_sessions"),
            "agent_sessions",
            ["session_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if _postgres():
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"""
                CREATE POLICY {table}_org_isolation ON {table}
                USING (
                  '*' = ANY(choregos_current_orgs())
                  OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
                )
                """
            )


def downgrade() -> None:
    with op.batch_alter_table("cost_ledger") as batch:
        batch.drop_constraint(op.f("fk_cost_ledger_session_id_agent_sessions"), type_="foreignkey")
        batch.drop_index(op.f("ix_cost_ledger_session_id"))
        batch.drop_column("session_id")
    for table in reversed(TABLES):
        if _postgres():
            op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    for column in ("session_id", "org_id"):
        op.drop_index(op.f(f"ix_agent_session_events_{column}"), table_name="agent_session_events")
    op.drop_table("agent_session_events")
    for column in ("user_id", "project_id", "org_id"):
        op.drop_index(op.f(f"ix_agent_sessions_{column}"), table_name="agent_sessions")
    op.drop_table("agent_sessions")
