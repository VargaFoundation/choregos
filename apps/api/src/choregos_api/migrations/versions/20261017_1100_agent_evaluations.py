# SPDX-License-Identifier: Apache-2.0
"""Live evaluations of an agent revision (S40-02d, ADR 0071): `agent_evaluations`

One row per evaluation started: the revision, the project it ran in, the trials it started and their
budgets. Under forced row-level security like the registry's tables.

Revision ID: a8c0e2b4d6f8
Revises: f6b8d0a2c4e6
Create Date: 2026-10-17 11:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "a8c0e2b4d6f8"
down_revision: str | None = "f6b8d0a2c4e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "agent_evaluations"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("budget_usd", sa.Float(), nullable=False),
        sa.Column("started_by", sa.String(length=200), nullable=True),
        sa.Column("cases", Json(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], name=op.f("fk_agent_evaluations_agent_id_agents"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_agent_evaluations_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_agent_evaluations_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_evaluations")),
    )
    op.create_index(op.f("ix_agent_evaluations_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_agent_evaluations_agent_id"), TABLE, ["agent_id"], unique=False)
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
    op.drop_index(op.f("ix_agent_evaluations_agent_id"), table_name=TABLE)
    op.drop_index(op.f("ix_agent_evaluations_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
