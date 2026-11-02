# SPDX-License-Identifier: Apache-2.0
"""A work item records who filed it in its tracker (#1179)

`work_items.reporter`: nullable, written once at birth from the tracker (Jira's reporter, GitHub's or
GitLab's author) or the console (the person's e-mail). Null on every existing row: the tracker was
not asked before. The downgrade drops the column.

Revision ID: c6e8a0b2d4f1
Revises: c6e8a0b2d4f6
Create Date: 2026-11-02 17:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6e8a0b2d4f1"
down_revision: str | None = "c6e8a0b2d4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.add_column(sa.Column("reporter", sa.String(320), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.drop_column("reporter")
