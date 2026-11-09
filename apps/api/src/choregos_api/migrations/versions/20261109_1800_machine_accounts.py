# SPDX-License-Identifier: Apache-2.0
"""Machine accounts: `machine_accounts` (S62-01, ADR 0131)

One row per application that calls under its own name, under forced RLS like `trigger_tokens`. The
downgrade drops the table and loses which `users` rows were machine accounts' and their budgets; the
`users` rows, their tokens and their memberships stay.

Revision ID: a7c9e1b3d5f7
Revises: d2f4a6c8e0b1
Create Date: 2026-11-09 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1b3d5f7"
down_revision: str | None = "d2f4a6c8e0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "machine_accounts",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("writes_per_day", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_machine_accounts_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_machine_accounts_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_machine_accounts_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_machine_accounts")),
        sa.UniqueConstraint("project_id", "slug", name="uq_machine_accounts_project_slug"),
    )
    op.create_index(op.f("ix_machine_accounts_org_id"), "machine_accounts", ["org_id"], unique=False)
    op.create_index(op.f("ix_machine_accounts_project_id"), "machine_accounts", ["project_id"], unique=False)
    op.create_index(op.f("ix_machine_accounts_user_id"), "machine_accounts", ["user_id"], unique=False)

    if _postgres():
        op.execute("ALTER TABLE machine_accounts ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE machine_accounts FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY machine_accounts_org_isolation ON machine_accounts
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS machine_accounts_org_isolation ON machine_accounts")
    op.drop_index(op.f("ix_machine_accounts_user_id"), table_name="machine_accounts")
    op.drop_index(op.f("ix_machine_accounts_project_id"), table_name="machine_accounts")
    op.drop_index(op.f("ix_machine_accounts_org_id"), table_name="machine_accounts")
    op.drop_table("machine_accounts")
