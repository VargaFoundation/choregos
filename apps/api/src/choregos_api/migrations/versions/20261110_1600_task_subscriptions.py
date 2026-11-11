# SPDX-License-Identifier: Apache-2.0
"""Task subscriptions: `task_subscriptions` (S63-13, ADR 0137)

Under forced RLS on the organisation, and a RESTRICTIVE policy that lets only the platform scope (the
scheduler) and the subscriber read a row. The downgrade drops the table and loses who subscribed to what.

Revision ID: e3f5a7c9e1b3
Revises: e0d2f4a6c8e0
Create Date: 2026-11-10 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "e3f5a7c9e1b3"
down_revision: str | None = "e0d2f4a6c8e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "task_subscriptions",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("task", sa.String(length=63), nullable=False),
        sa.Column("trigger", Json(), nullable=False),
        sa.Column("delegation_id", sa.String(length=36), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_window", sa.String(length=32), nullable=True),
        sa.Column("seen_keys", Json(), nullable=False),
        sa.Column("last_refusal", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_task_subscriptions_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["assistant_installations.id"],
            name=op.f("fk_task_subscriptions_installation_id_assistant_installations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_task_subscriptions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_subscriptions")),
        sa.UniqueConstraint("installation_id", "user_id", "task", name="uq_task_subscriptions"),
    )
    for column in ("org_id", "installation_id", "user_id"):
        op.create_index(op.f(f"ix_task_subscriptions_{column}"), "task_subscriptions", [column], unique=False)
    if _postgres():
        op.execute("ALTER TABLE task_subscriptions ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE task_subscriptions FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY task_subscriptions_org_isolation ON task_subscriptions
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )
        op.execute(
            """
            CREATE POLICY task_subscriptions_owner ON task_subscriptions AS RESTRICTIVE
            USING (
              '*' = ANY(choregos_current_orgs())
              OR user_id = choregos_utilisateur_courant()
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS task_subscriptions_owner ON task_subscriptions")
        op.execute("DROP POLICY IF EXISTS task_subscriptions_org_isolation ON task_subscriptions")
    for column in ("user_id", "installation_id", "org_id"):
        op.drop_index(op.f(f"ix_task_subscriptions_{column}"), table_name="task_subscriptions")
    op.drop_table("task_subscriptions")
