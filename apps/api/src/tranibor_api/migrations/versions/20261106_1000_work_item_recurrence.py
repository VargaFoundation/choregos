# SPDX-License-Identifier: Apache-2.0
"""A reopened ticket restarts its finished work item (C1, D2C plan)

`work_items.recurrence`: how many times the work item came back after it ended, 0 on every existing
row. `work_items.recurred_at`: when its current pass began, null until it first comes back; the pass's
attempts budget and ticket spend count from there. The downgrade drops both.

Revision ID: c1a3e5b7d9f2
Revises: e7a9c1d3f5b8
Create Date: 2026-11-06 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1a3e5b7d9f2"
down_revision: str | None = "e7a9c1d3f5b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.add_column(sa.Column("recurrence", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("recurred_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.drop_column("recurred_at")
        batch.drop_column("recurrence")
