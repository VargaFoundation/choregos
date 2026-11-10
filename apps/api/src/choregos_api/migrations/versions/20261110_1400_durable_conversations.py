"""Durable conversations: the columns of `agent_sessions` that make a session resumable (S63-04, ADR 0134)

A conversation is an extended session: the table keeps its name and gains `kind`, `installation_id`,
`title`, `context`, `resolved_subjects`, `last_activity_at`, `retention_until`, `agent_revision`, `turns`,
`max_usd` and `idle_close_minutes`. `installation_id` carries no foreign key yet: the installations table
is created by S63-02, and the key is added by whichever of the two stories lands second.

Every session open at the migration is closed (`reason: migrated`), in SQL that runs on SQLite and on
PostgreSQL. The spend of their keys is NOT booked here (the gateway holds it, and a migration does not call
the gateway): the keys end at their own TTL, at most `SESSION_TTL_S` later for a prompt session.

On PostgreSQL a RESTRICTIVE policy on `agent_sessions` lets the platform scope (`*`) and the opener read a
conversation, whatever roles another person holds in the organisation; the transcript follows through a
policy on `agent_session_events` that asks for the session under RLS.

The downgrade drops the policies and the columns, and deletes the conversations first: a downgraded
instance has no key per turn and would read them as sessions that never close.

Revision ID: c4b6d8f0a2e3
Revises: c2a4e6b8d0f1
Create Date: 2026-11-10 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4b6d8f0a2e3"
down_revision: str | None = "c2a4e6b8d0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SESSIONS_OWNER_ONLY = """
CREATE POLICY agent_sessions_conversation_owner ON agent_sessions AS RESTRICTIVE
USING (
  '*' = ANY(choregos_current_orgs())
  OR kind <> 'conversation'
  OR user_id = choregos_utilisateur_courant()
)
"""
EVENTS_FOLLOW = """
CREATE POLICY agent_session_events_conversation_owner ON agent_session_events AS RESTRICTIVE
USING (EXISTS (SELECT 1 FROM agent_sessions s WHERE s.id = agent_session_events.session_id))
"""

NEW_COLUMNS = (
    "idle_close_minutes",
    "max_usd",
    "turns",
    "agent_revision",
    "retention_until",
    "last_activity_at",
    "resolved_subjects",
    "context",
    "title",
    "installation_id",
    "kind",
)


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=16), server_default="session", nullable=False))
        batch.add_column(sa.Column("installation_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("title", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("context", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("resolved_subjects", sa.JSON(), server_default="[]", nullable=False))
        batch.add_column(sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("agent_revision", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("turns", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("max_usd", sa.Float(), nullable=True))
        batch.add_column(sa.Column("idle_close_minutes", sa.Integer(), nullable=True))
        batch.create_index(batch.f("ix_agent_sessions_installation_id"), ["installation_id"], unique=False)
    if _postgres():  # the rows of every organisation, under forced RLS (S39-27)
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
    op.execute(
        "UPDATE agent_sessions SET status = 'closed', reason = 'migrated', closed_at = CURRENT_TIMESTAMP "
        "WHERE status = 'open'"
    )
    if _postgres():
        op.execute(SESSIONS_OWNER_ONLY)
        op.execute(EVENTS_FOLLOW)


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS agent_session_events_conversation_owner ON agent_session_events")
        op.execute("DROP POLICY IF EXISTS agent_sessions_conversation_owner ON agent_sessions")
    if _postgres():  # the rows of every organisation, under forced RLS (S39-27)
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
    op.execute(
        "DELETE FROM agent_session_events WHERE session_id IN "
        "(SELECT id FROM agent_sessions WHERE kind = 'conversation')"
    )
    op.execute("DELETE FROM agent_sessions WHERE kind = 'conversation'")
    with op.batch_alter_table("agent_sessions") as batch:
        batch.drop_index(batch.f("ix_agent_sessions_installation_id"))
        for column in NEW_COLUMNS:
            batch.drop_column(column)
