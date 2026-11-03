# SPDX-License-Identifier: Apache-2.0
"""Several workflows per project: who published a version, and the routing rules (ADR 0031)

- `workflow_defs.created_by`: the version history says who published;
- `projects.workflow_routing`: the rules that choose the workflow of a ticket coming from a tracker.

Revision ID: b8d0f2a4c6e9
Revises: a7c9e1f3b5d8
Create Date: 2026-10-05 18:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "b8d0f2a4c6e9"
down_revision: str | None = "a7c9e1f3b5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_defs") as batch:
        batch.add_column(sa.Column("created_by", sa.String(length=200), nullable=True))
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("workflow_routing", Json(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("workflow_routing")
    with op.batch_alter_table("workflow_defs") as batch:
        batch.drop_column("created_by")
