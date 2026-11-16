# SPDX-License-Identifier: Apache-2.0
"""Who a waiting human request is reassigned to (ADR 0153): `human_requests.assignee`

An organisation administrator may reassign a waiting request to a person or a group. The override is
kept on the request row, as JSON `{user, group}`, and replaces the workflow's actor for that request
only. Null for every existing row: the workflow's actor answers, as before.

A nullable column only: no row rewritten, and nothing of row-level security moves. The downgrade
drops it.

Revision ID: f9b1d3e5a7c0
Revises: e8a0c2d4f6b9
Create Date: 2026-11-12 13:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "f9b1d3e5a7c0"
down_revision: str | None = "e8a0c2d4f6b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("human_requests") as batch:
        batch.add_column(sa.Column("assignee", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("human_requests") as batch:
        batch.drop_column("assignee")
