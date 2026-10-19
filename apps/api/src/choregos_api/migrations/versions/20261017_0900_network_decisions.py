# SPDX-License-Identifier: Apache-2.0
"""Network decisions: `network_decisions` (ADR 0070)

What the egress proxy and the runner let through or refused, aggregated per run, under forced RLS
like the rest of the registry.

Revision ID: d7f0a2c4e6b8
Revises: f2a4c6e8b0d1
Create Date: 2026-10-17 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7f0a2c4e6b8"
down_revision: str | None = "f2a4c6e8b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "network_decisions",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=True),
        sa.Column("agent_slug", sa.String(length=64), nullable=True),
        sa.Column("layer", sa.String(length=16), nullable=False),
        sa.Column("host", sa.String(length=253), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("bytes_up", sa.BigInteger(), nullable=False),
        sa.Column("bytes_down", sa.BigInteger(), nullable=False),
        sa.Column("first_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_network_decisions_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_network_decisions_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_network_decisions_run_id_runs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_network_decisions")),
        sa.UniqueConstraint(
            "run_id", "layer", "host", "port", "decision", "reason", name="uq_network_decisions_key"
        ),
    )
    op.create_index(op.f("ix_network_decisions_org_id"), "network_decisions", ["org_id"], unique=False)
    op.create_index(op.f("ix_network_decisions_project_id"), "network_decisions", ["project_id"], unique=False)
    op.create_index(op.f("ix_network_decisions_run_id"), "network_decisions", ["run_id"], unique=False)
    op.create_index("ix_network_decisions_org_last", "network_decisions", ["org_id", "last_at"], unique=False)

    if _postgres():
        op.execute("ALTER TABLE network_decisions ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE network_decisions FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY network_decisions_org_isolation ON network_decisions
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS network_decisions_org_isolation ON network_decisions")
    op.drop_index("ix_network_decisions_org_last", table_name="network_decisions")
    op.drop_index(op.f("ix_network_decisions_run_id"), table_name="network_decisions")
    op.drop_index(op.f("ix_network_decisions_project_id"), table_name="network_decisions")
    op.drop_index(op.f("ix_network_decisions_org_id"), table_name="network_decisions")
    op.drop_table("network_decisions")
