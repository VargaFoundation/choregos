# SPDX-License-Identifier: Apache-2.0
"""A knowledge base remembers the project it was created from (S39-14): `knowledge_bases.owner_project`

Revision ID: c3e5a7b9d1f2
Revises: f1b3d5a7c9e2
Create Date: 2026-10-15 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a7b9d1f2"
down_revision: str | None = "f1b3d5a7c9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(sa.Column("owner_project", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_column("owner_project")
