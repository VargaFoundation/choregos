# SPDX-License-Identifier: Apache-2.0
"""A run keeps how its pod ended and the tail of its log, redacted (S36-02): `runs.log_archive`

Revision ID: 9e41c7a0d35b
Revises: c5e7a9b1d3f5
Create Date: 2026-10-12 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "9e41c7a0d35b"
down_revision: str | None = "c5e7a9b1d3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("log_archive", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("log_archive")
