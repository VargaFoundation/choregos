# SPDX-License-Identifier: Apache-2.0
"""A tool call's ledger line says which door it came through (S57-06, ADR 0122 §4)

`cost_ledger.origin` (`model`, `script`, `job`) and `cost_ledger.script_sha256`: null on every
existing row. No backfill: a line booked before this revision did not record its door, and the cost
report shows it as one without (`—`), the way it shows a playbook run's line without an agent. The
downgrade drops both.

Revision ID: b3d5f7a9c1e2
Revises: d8f0b2c4e6a1
Create Date: 2026-11-04 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3d5f7a9c1e2"
down_revision: str | None = "d8f0b2c4e6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cost_ledger") as batch:
        batch.add_column(sa.Column("origin", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("script_sha256", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("cost_ledger") as batch:
        batch.drop_column("script_sha256")
        batch.drop_column("origin")
