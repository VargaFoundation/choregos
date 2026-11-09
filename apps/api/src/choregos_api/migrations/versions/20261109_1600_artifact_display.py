# SPDX-License-Identifier: Apache-2.0
"""An artifact says how it is shown (ADR 0129)

`artifacts.display`: the snapshot of its presentation, null on every existing row, which then gets the
display of its well-known name at read time. Outside the digest. The downgrade drops it.

Revision ID: d2f4a6c8e0b1
Revises: f242dbc24f07
Create Date: 2026-11-09 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "d2f4a6c8e0b1"
down_revision: str | None = "f242dbc24f07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.add_column(sa.Column("display", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("artifacts") as batch:
        batch.drop_column("display")
