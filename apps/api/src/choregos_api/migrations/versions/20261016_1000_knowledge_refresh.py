# SPDX-License-Identifier: Apache-2.0
"""Knowledge source refresh schedule (S39-19, ADR 0051): mode, cron, time zone, next sync, request

`refresh_mode` is `interval` (the existing behaviour), `cron` or `manual`. A null `next_sync_at`
means the source is due, so no data migration is needed.

Revision ID: b2c4d6e8f0a1
Revises: e7a9c1b3d5f7
Create Date: 2026-10-16 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c4d6e8f0a1"
down_revision: str | None = "e7a9c1b3d5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "knowledge_sources"


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        batch.add_column(sa.Column("refresh_mode", sa.String(8), nullable=False, server_default="interval"))
        batch.add_column(sa.Column("refresh_cron", sa.String(100), nullable=True))
        batch.add_column(sa.Column("refresh_timezone", sa.String(64), nullable=False, server_default="UTC"))
        batch.add_column(sa.Column("next_sync_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("sync_requested_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch:
        for column in ("sync_requested_at", "next_sync_at", "refresh_timezone", "refresh_cron", "refresh_mode"):
            batch.drop_column(column)
