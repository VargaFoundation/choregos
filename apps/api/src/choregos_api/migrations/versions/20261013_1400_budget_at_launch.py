# SPDX-License-Identifier: Apache-2.0
"""The budget given at launch (S27-06, ADR 0047): `work_items.budget_usd`

Revision ID: 2c4e6a8b0d1f
Revises: 1b3d5f7a9c0e
Create Date: 2026-10-13 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2c4e6a8b0d1f"
down_revision: str | None = "1b3d5f7a9c0e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.add_column(sa.Column("budget_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.drop_column("budget_usd")
