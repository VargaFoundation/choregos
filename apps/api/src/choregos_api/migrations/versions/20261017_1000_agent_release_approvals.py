# SPDX-License-Identifier: Apache-2.0
"""Approvals before an agent revision reaches production (S40-02b, ADR 0071)

`agent_releases` gains the number of approvals a release waited for and what each approver answered.
Existing rows (the S40-02a history and the baselines) did not wait for anyone: 0 and no decisions.

Revision ID: f6b8d0a2c4e6
Revises: c1e3a5b7d9f2
Create Date: 2026-10-17 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "f6b8d0a2c4e6"
down_revision: str | None = "c1e3a5b7d9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_releases") as batch:
        batch.add_column(sa.Column("approvals_required", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("decisions", Json(), nullable=True))
    op.execute("UPDATE agent_releases SET decisions = '[]'")
    with op.batch_alter_table("agent_releases") as batch:
        batch.alter_column("decisions", existing_type=Json(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("agent_releases") as batch:
        batch.drop_column("decisions")
        batch.drop_column("approvals_required")
