# SPDX-License-Identifier: Apache-2.0
"""A person's devices are told by Web Push: their subscriptions (ADR 0194)

`push_subscriptions`: one row per device, named by its push endpoint (unique: one device, one
owner). Under RLS like `user_sessions` (ADR 0026): read and written by its owner
(`app.current_user`), or in platform scope (the delivery, which reads every person's devices, and
the registration of an endpoint that another person held). No `org_id`: the device is the person's.
The downgrade drops the table: the devices stop being told, nothing else changes.

Revision ID: a3c5e7091b2d
Revises: e2a4c6b8d0f3
Create Date: 2026-11-19 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c5e7091b2d"
down_revision: str | None = "e2a4c6b8d0f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WHOLE = "'*' = ANY(choregos_current_orgs())"
OWNER = "user_id = choregos_utilisateur_courant()"
# Not `choregos_utilisateur_visible`: an administrator who sees a person does not see their devices.
POLICY = f"{WHOLE} OR {OWNER}"


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("endpoint", sa.String(length=2048), nullable=False),
        sa.Column("p256dh", sa.String(length=128), nullable=False),
        sa.Column("auth", sa.String(length=64), nullable=False),
        sa.Column("device_name", sa.String(length=80), nullable=False),
        sa.Column("kinds", sa.JSON(), nullable=True),
        sa.Column("last_delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failing_since", sa.DateTime(timezone=True), nullable=True),
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
            ["user_id"], ["users.id"], name=op.f("fk_push_subscriptions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_subscriptions")),
        sa.UniqueConstraint("endpoint", name=op.f("uq_push_subscriptions_endpoint")),
    )
    op.create_index(op.f("ix_push_subscriptions_user_id"), "push_subscriptions", ["user_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE push_subscriptions FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY push_subscriptions_owner ON push_subscriptions USING ({POLICY}) WITH CHECK ({POLICY})"
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_push_subscriptions_user_id"), table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
