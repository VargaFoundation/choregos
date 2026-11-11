# SPDX-License-Identifier: Apache-2.0
"""The task a read belongs to (S63-16): `assistant_reads.task`

A poll reads before any work item exists, so its line names the task and the delegation; a task run's
line names the work item too. Null for a conversation's line.

Revision ID: a7f7c0f0e0d7
Revises: a3e3c0f0e0d3
Create Date: 2026-11-11 11:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7f7c0f0e0d7"
down_revision: str | None = "a3e3c0f0e0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assistant_reads") as batch:
        batch.add_column(sa.Column("task", sa.String(63), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assistant_reads") as batch:
        batch.drop_column("task")
