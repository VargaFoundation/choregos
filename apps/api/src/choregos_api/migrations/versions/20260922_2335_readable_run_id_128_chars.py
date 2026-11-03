# SPDX-License-Identifier: Apache-2.0
"""A run's identifier is readable, not a UUID: 128 characters

`<ticket>-<transition>-<attempt>` exceeds the 36 characters of a UUID. PostgreSQL
answered "value too long for type character varying(36)" at the first step; SQLite,
which does not enforce declared lengths, said nothing, hence a defect invisible
everywhere except in real life. The columns that reference a run follow.

Revision ID: 0df3d8ee7496
Revises: 7bdeea6732b0
Create Date: 2026-09-22 23:35:30.487643+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0df3d8ee7496'
down_revision: str | None = '7bdeea6732b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


COLUMNS = [
    # (table, column, nullable)
    ("cost_ledger", "run_id", True),
    ("findings", "origin_run_id", True),
    ("gateway_keys", "run_id", True),
    ("run_events", "run_id", False),
    ("runs", "id", False),
]


def _change(to: int, from_: int) -> None:
    # `batch_alter_table` and not `alter_column`: SQLite cannot do
    # `ALTER COLUMN ... TYPE`, and the test that compares migrations and models runs on it.
    # On PostgreSQL, the "batch" form falls back to an ordinary ALTER.
    for table, column, nullable in COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                column,
                existing_type=sa.String(length=from_),
                type_=sa.String(length=to),
                existing_nullable=nullable,
            )


def upgrade() -> None:
    _change(to=128, from_=36)


def downgrade() -> None:
    _change(to=36, from_=128)
