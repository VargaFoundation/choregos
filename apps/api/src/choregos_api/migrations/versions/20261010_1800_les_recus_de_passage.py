# SPDX-License-Identifier: Apache-2.0
"""Les reçus de passage entre étapes (S25-04) : `hand_off_receipts`

Une ligne par sortie produite ou lue, sous son empreinte ; jamais modifiée. Sous la RLS forcée,
comme le reste.

Revision ID: a3c5e7f9b1d2
Revises: f8a0b2c4d6e9
Create Date: 2026-10-10 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c5e7f9b1d2"
down_revision: str | None = "f8a0b2c4d6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "hand_off_receipts"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("output", sa.String(length=128), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=True),
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
            name=op.f("fk_hand_off_receipts_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_hand_off_receipts_work_item_id_work_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hand_off_receipts")),
        sa.UniqueConstraint(
            "work_item_id", "output", "digest", "run_id", "kind", name="uq_hand_off_receipts_passage"
        ),
    )
    op.create_index(op.f("ix_hand_off_receipts_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_hand_off_receipts_work_item_id"), TABLE, ["work_item_id"], unique=False)

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
    op.drop_index(op.f("ix_hand_off_receipts_work_item_id"), table_name=TABLE)
    op.drop_index(op.f("ix_hand_off_receipts_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
