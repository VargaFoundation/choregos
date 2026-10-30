# SPDX-License-Identifier: Apache-2.0
"""A work item records the restricted knowledge bases its runs were served (S42-11f, ADR 0114)

`work_items.restricted_knowledge`: a nullable list of base slugs, written when a run is pinned a
restricted base and only ever added to. Null on every existing row: no work item was marked before.
The downgrade drops the column.

Revision ID: b4d6f8a0c2e7
Revises: e2a4c6e8f0b3
Create Date: 2026-10-30 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "b4d6f8a0c2e7"
down_revision: str | None = "e2a4c6e8f0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.add_column(sa.Column("restricted_knowledge", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.drop_column("restricted_knowledge")
