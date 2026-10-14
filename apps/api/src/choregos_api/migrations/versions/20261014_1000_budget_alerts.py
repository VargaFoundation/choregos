# SPDX-License-Identifier: Apache-2.0
"""Budget alerts for the inbox (S29-04): `budget_alerts`

One row per budget, period and threshold (the unique key makes the alert idempotent). Under forced
row-level security, like the other tables that carry an organisation.

Revision ID: a9c1e3f5b7d9
Revises: 2c4e6a8b0d1f
Create Date: 2026-10-14 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9c1e3f5b7d9"
down_revision: str | None = "2c4e6a8b0d1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "budget_alerts"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("spent_usd", sa.Float(), nullable=False),
        sa.Column("budget_usd", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=200), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_budget_alerts_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_budget_alerts")),
        sa.UniqueConstraint(
            "org_id", "scope", "subject", "period", "period_key", "threshold", name="uq_budget_alerts_threshold"
        ),
    )
    op.create_index(op.f("ix_budget_alerts_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_budget_alerts_status"), TABLE, ["status"], unique=False)
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
    op.drop_index(op.f("ix_budget_alerts_status"), table_name=TABLE)
    op.drop_index(op.f("ix_budget_alerts_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
