# SPDX-License-Identifier: Apache-2.0
"""Received webhook events: `received_events` (S42-06f)

The webhook events a project received and what the platform did with each, under forced RLS like
`network_decisions`. A summary from typed fields only: no payload, header, comment or email.

Revision ID: c783c8336a18
Revises: b8d0f2a4c6e1
Create Date: 2026-10-28 23:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c783c8336a18"
down_revision: str | None = "b8d0f2a4c6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "received_events",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=True),
        sa.Column("work_item_key", sa.String(length=255), nullable=True),
        sa.Column("source", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("delivery_id", sa.String(length=255), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_received_events_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_received_events_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_received_events_work_item_id_work_items"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_received_events")),
    )
    op.create_index(op.f("ix_received_events_org_id"), "received_events", ["org_id"], unique=False)
    op.create_index(op.f("ix_received_events_project_id"), "received_events", ["project_id"], unique=False)
    op.create_index(
        "ix_received_events_project_received", "received_events", ["project_id", "received_at"], unique=False
    )

    if _postgres():
        op.execute("ALTER TABLE received_events ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE received_events FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY received_events_org_isolation ON received_events
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS received_events_org_isolation ON received_events")
    op.drop_index("ix_received_events_project_received", table_name="received_events")
    op.drop_index(op.f("ix_received_events_project_id"), table_name="received_events")
    op.drop_index(op.f("ix_received_events_org_id"), table_name="received_events")
    op.drop_table("received_events")
