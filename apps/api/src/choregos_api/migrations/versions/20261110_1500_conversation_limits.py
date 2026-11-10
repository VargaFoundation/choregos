"""Conversation limits: turns, retention, compression's archive and the turn lease (S63-04b, ADR 0134)

`agent_sessions` gains `max_turns`, `retention_days`, `archived_messages` (the turns compression replaced by
a summary in `messages`) and `turn_lease_until` (set while a turn runs: a second turn is refused).

Revision ID: d4e6f8a0c2e4
Revises: d5a7c9e1f3b5
Create Date: 2026-11-10 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6f8a0c2e4"
down_revision: str | None = "d5a7c9e1f3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch:
        batch.add_column(sa.Column("max_turns", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("retention_days", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("archived_messages", sa.JSON(), server_default="[]", nullable=False))
        batch.add_column(sa.Column("turn_lease_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch:
        for column in ("turn_lease_until", "archived_messages", "retention_days", "max_turns"):
            batch.drop_column(column)
