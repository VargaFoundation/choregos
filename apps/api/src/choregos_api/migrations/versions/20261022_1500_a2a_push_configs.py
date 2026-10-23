# SPDX-License-Identifier: Apache-2.0
"""A2A push registrations: `a2a_push_configs` (S40-23d, ADR 0078 §5)

The URLs an A2A client registered to hear of a task, bound to the `chg_` token that registered them
(deleted with it), with the cursor of the last event delivered. The signing secret is a sealed value
of `org_secrets`, never a column here. Under forced RLS like the rest of the organisation's rows.

Revision ID: d9f1b3c5e7a2
Revises: c7e9a1b3d5f8
Create Date: 2026-10-22 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9f1b3c5e7a2"
down_revision: str | None = "c7e9a1b3d5f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "a2a_push_configs"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("api_token_id", sa.String(length=36), nullable=False),
        sa.Column("client", sa.String(length=255), nullable=False),
        sa.Column("config_id", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("cursor_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_event_id", sa.String(length=36), nullable=True),
        sa.Column("last_view", sa.String(length=400), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=400), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_a2a_push_configs_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_a2a_push_configs_work_item_id_work_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["api_token_id"],
            ["api_tokens.id"],
            name=op.f("fk_a2a_push_configs_api_token_id_api_tokens"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_a2a_push_configs")),
        sa.UniqueConstraint("work_item_id", "client", "config_id", name="uq_a2a_push_configs_client_config"),
    )
    for column in ("org_id", "work_item_id", "api_token_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column], unique=False)

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
    for column in ("api_token_id", "work_item_id", "org_id"):
        op.drop_index(op.f(f"ix_{TABLE}_{column}"), table_name=TABLE)
    op.drop_table(TABLE)
