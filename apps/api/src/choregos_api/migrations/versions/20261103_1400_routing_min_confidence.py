# SPDX-License-Identifier: Apache-2.0
"""A project may refuse a decision model's answer below a confidence (ADR 0116, ADR 0121)

`projects.routing_min_confidence`: null on every existing row, which keeps every answer as before
this revision. The downgrade drops it.

Revision ID: d8f0b2c4e6a1
Revises: e4a6c8d0f2b5
Create Date: 2026-11-03 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f0b2c4e6a1"
down_revision: str | None = "e4a6c8d0f2b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("routing_min_confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("routing_min_confidence")
