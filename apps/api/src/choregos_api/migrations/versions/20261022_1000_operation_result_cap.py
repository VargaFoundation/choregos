# SPDX-License-Identifier: Apache-2.0
"""An operation may declare its own result cap (S40-17): `connector_operations.result_max_bytes`

The organisation sets it per operation; null keeps the platform's default (#755). Nullable, no
backfill: every existing operation keeps the default it had.

Revision ID: b7d1f3a5c9e2
Revises: e4a6c8d0b2f1
Create Date: 2026-10-22 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d1f3a5c9e2"
down_revision: str | None = "e4a6c8d0b2f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("connector_operations") as batch:
        batch.add_column(sa.Column("result_max_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("connector_operations") as batch:
        batch.drop_column("result_max_bytes")
