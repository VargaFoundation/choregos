# SPDX-License-Identifier: Apache-2.0
"""A cancel that reaches another replica: the flag on the conversation (S63-23)

`agent_sessions.turn_cancel_requested_at` is set by the replica that receives a cancel while another replica
runs the turn; the running turn polls it and stops.

Revision ID: b3a3c0f0e0d3
Revises: a7f7c0f0e0d7
Create Date: 2026-11-12 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3a3c0f0e0d3"
down_revision: str | None = "a7f7c0f0e0d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch:
        batch.add_column(sa.Column("turn_cancel_requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch:
        batch.drop_column("turn_cancel_requested_at")
