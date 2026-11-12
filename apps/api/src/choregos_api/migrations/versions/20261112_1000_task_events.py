# SPDX-License-Identifier: Apache-2.0
"""The events routed to a `trigger` task: `task_events` (S63-21, ADR 0137 point 2)

A journal under forced RLS on the organisation: the door of an installation's task events and the
organisation's managers read it. The downgrade drops the table and loses the journal.

Revision ID: b1a1c0f0e0d1
Revises: a7f7c0f0e0d7
Create Date: 2026-11-12 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1a1c0f0e0d1"
down_revision: str | None = "a7f7c0f0e0d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "task_events",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("task", sa.String(length=63), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("work_item_key", sa.String(length=255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_task_events_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["assistant_installations.id"],
            name=op.f("fk_task_events_installation_id_assistant_installations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_task_events_user_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_events")),
    )
    op.create_index(op.f("ix_task_events_org_id"), "task_events", ["org_id"], unique=False)
    op.create_index(op.f("ix_task_events_installation_id"), "task_events", ["installation_id"], unique=False)
    op.create_index(
        "ix_task_events_installation_received", "task_events", ["installation_id", "received_at"], unique=False
    )
    if _postgres():
        op.execute("ALTER TABLE task_events ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE task_events FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY task_events_org_isolation ON task_events
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS task_events_org_isolation ON task_events")
    op.drop_index("ix_task_events_installation_received", table_name="task_events")
    op.drop_index(op.f("ix_task_events_installation_id"), table_name="task_events")
    op.drop_index(op.f("ix_task_events_org_id"), table_name="task_events")
    op.drop_table("task_events")
