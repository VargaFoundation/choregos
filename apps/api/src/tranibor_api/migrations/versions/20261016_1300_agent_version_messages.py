# SPDX-License-Identifier: Apache-2.0
"""Agent version messages (S40-12, ADR 0076): `agent_versions.message` and `restored_from`.

A version carries the author's message and, when it is a restore, the version it copies. Both are
nullable and nothing is backfilled: a version published before has no message, and says so.

Revision ID: e6b8d0f2a4c5
Revises: d4f6a8c0e2b3
Create Date: 2026-10-16 13:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6b8d0f2a4c5"
down_revision: str | None = "d4f6a8c0e2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_versions") as batch:
        batch.add_column(sa.Column("message", sa.Text(), nullable=True))
        batch.add_column(sa.Column("restored_from", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_versions") as batch:
        batch.drop_column("restored_from")
        batch.drop_column("message")
