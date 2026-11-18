# SPDX-License-Identifier: Apache-2.0
"""An organisation-level line in the cost ledger (S42-10e, ADR 0092)

A knowledge-base distillation whose base has no owning project is the organisation's spend, not a
project's. `cost_ledger.project_id` becomes nullable and `cost_ledger.org_id` (nullable) names the
organisation of such a line; exactly one of the two is set. The row-level-security policy judges the
organisation from the project, or from `org_id` when there is none. No backfill: existing lines all
have a project.

The downgrade deletes the organisation-level lines (they have no project to go back to).

Revision ID: a8c0e2f4b6d9
Revises: a7c9e1b3d5f6
Create Date: 2026-10-27 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a8c0e2f4b6d9"
down_revision: str | None = "a7c9e1b3d5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _policy(visible: str) -> None:
    op.execute("DROP POLICY IF EXISTS cost_ledger_org_isolation ON cost_ledger")
    op.execute(
        f"""
        CREATE POLICY cost_ledger_org_isolation ON cost_ledger
        USING (
          '*' = ANY(choregos_current_orgs())
          OR {visible} = ANY(choregos_current_orgs())
        )
        """
    )


def upgrade() -> None:
    # `batch_alter_table`: SQLite cannot add a foreign key or drop NOT NULL in place.
    with op.batch_alter_table("cost_ledger") as batch:
        batch.add_column(sa.Column("org_id", sa.String(length=36), nullable=True))
        batch.alter_column("project_id", existing_type=sa.String(length=36), nullable=True)
        batch.create_index(op.f("ix_cost_ledger_org_id"), ["org_id"], unique=False)
        batch.create_foreign_key(
            op.f("fk_cost_ledger_org_id_organizations"),
            "organizations",
            ["org_id"],
            ["id"],
            ondelete="CASCADE",
        )
    if _postgres():
        _policy("COALESCE(choregos_project_org(project_id), choregos_org_slug(org_id))")


def downgrade() -> None:
    if _postgres():
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
        op.execute("DELETE FROM cost_ledger WHERE project_id IS NULL")
        _policy("choregos_project_org(project_id)")
    else:
        op.execute("DELETE FROM cost_ledger WHERE project_id IS NULL")
    with op.batch_alter_table("cost_ledger") as batch:
        batch.drop_constraint(op.f("fk_cost_ledger_org_id_organizations"), type_="foreignkey")
        batch.drop_index(op.f("ix_cost_ledger_org_id"))
        batch.alter_column("project_id", existing_type=sa.String(length=36), nullable=False)
        batch.drop_column("org_id")
