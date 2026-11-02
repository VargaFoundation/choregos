# SPDX-License-Identifier: Apache-2.0
"""A project chooses how a work item finds its workflow, and may let a small model choose (ADR 0116)

`projects.routing_strategy`: `labels` on every existing row, the behaviour before this revision;
`projects.routing_hints`: what the model reads about each workflow, empty. The downgrade drops both.

Revision ID: c6e8a0b2d4f6
Revises: b4d6f8a0c2e7
Create Date: 2026-11-02 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "c6e8a0b2d4f6"
down_revision: str | None = "b4d6f8a0c2e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("routing_strategy", sa.String(16), nullable=False, server_default="labels")
        )
        batch.add_column(sa.Column("routing_hints", Json(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("routing_hints")
        batch.drop_column("routing_strategy")
