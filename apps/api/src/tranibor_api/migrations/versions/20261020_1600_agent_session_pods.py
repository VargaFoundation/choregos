# SPDX-License-Identifier: Apache-2.0
"""A hosted session's pod: `agent_sessions.pod` and `agent_sessions.stage_input` (S41-11, ADR 0080 §10)

`pod` is the pod a hosted agent's session runs in, as its workflow last wrote it: `{state, kind,
name, namespace}`, `state` being `requested`, `running` or `stopped`. `stage_input` is what the pod's
runner reads at start, without key or token. Both are null for a prompt or a voice session, and for
a hosted one before its first response.

Revision ID: 97b19c72c16e
Revises: f7b9d1e3a5c8
Create Date: 2026-10-20 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "97b19c72c16e"
down_revision: str | None = "f7b9d1e3a5c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch:
        batch.add_column(sa.Column("pod", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("stage_input", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent_sessions") as batch:
        batch.drop_column("stage_input")
        batch.drop_column("pod")
