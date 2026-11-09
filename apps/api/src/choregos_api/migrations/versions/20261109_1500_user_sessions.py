# SPDX-License-Identifier: Apache-2.0
"""A browser session is recorded, so that its owner can see it and end it (ADR 0130)

`user_sessions`: one row per session cookie, named by the cookie's `sid`. Under RLS like
`api_tokens`: a session is read by its owner (`app.current_user`) or within a scope that sees its
user, and written by its owner or in platform scope (the sign-in, before any scope).
`users.sessions_not_before`: the instant before which a cookie WITHOUT `sid` is refused.
The downgrade drops both; the cookies keep working, without a list.

Revision ID: f242dbc24f07
Revises: c1a3e5b7d9f2
Create Date: 2026-11-09 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f242dbc24f07"
down_revision: str | None = "c1a3e5b7d9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WHOLE = "'*' = ANY(choregos_current_orgs())"
READ = f"{WHOLE} OR user_id = choregos_utilisateur_courant() OR choregos_utilisateur_visible(user_id)"
WRITE = f"{WHOLE} OR user_id = choregos_utilisateur_courant()"


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
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
            ["user_id"], ["users.id"], name=op.f("fk_user_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
    )
    op.create_index(op.f("ix_user_sessions_user_id"), "user_sessions", ["user_id"])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("sessions_not_before", sa.DateTime(timezone=True), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE user_sessions ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE user_sessions FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY user_sessions_org_isolation ON user_sessions USING ({READ}) WITH CHECK ({WRITE})"
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("sessions_not_before")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_table("user_sessions")
