# SPDX-License-Identifier: Apache-2.0
"""An operation keeps the `outputSchema` its server declares (#755): `connector_operations.output_schema`

The broker holds a successful tool result to it before the agent reads it. Nullable, no backfill: the
next discovery of each connector fills it.

Revision ID: e4a6c8f0b2d5
Revises: 9d2e4f6a8b1c
Create Date: 2026-10-20 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "e4a6c8f0b2d5"
down_revision: str | None = "9d2e4f6a8b1c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("connector_operations") as batch:
        batch.add_column(sa.Column("output_schema", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("connector_operations") as batch:
        batch.drop_column("output_schema")
