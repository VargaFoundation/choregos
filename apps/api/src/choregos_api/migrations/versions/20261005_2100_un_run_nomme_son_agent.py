# SPDX-License-Identifier: Apache-2.0
"""Un run nomme l'agent du registre qui l'a fait, et sa version (ADR 0033)

Revision ID: a3b5c7d9e1f4
Revises: f2a4b6c8d0e3
Create Date: 2026-10-05 21:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b5c7d9e1f4"
down_revision: str | None = "f2a4b6c8d0e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("agent_slug", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("agent_version", sa.Integer(), nullable=True))
        batch.create_index(batch.f("ix_runs_agent_slug"), ["agent_slug"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_index(batch.f("ix_runs_agent_slug"))
        batch.drop_column("agent_version")
        batch.drop_column("agent_slug")
