# SPDX-License-Identifier: Apache-2.0
"""The run's chain (S40-22, ADR 0077 §2): `work_items.initiated_by`, `runs.initiated_by`, `runs.inputs_from`

Three nullable columns, no backfill: a work item or a run recorded before stays `NULL`, and the
access record says its initiator is unknown rather than guess one from `created_by`. The downgrade
drops the three columns and the chains they held; an upgrade after it starts them at `NULL` again.

Revision ID: c7e9a1b3d5f8
Revises: b7d1f3a5c9e2
Create Date: 2026-10-22 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "c7e9a1b3d5f8"
down_revision: str | None = "b7d1f3a5c9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.add_column(sa.Column("initiated_by", sa.String(255), nullable=True))
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("initiated_by", sa.String(255), nullable=True))
        batch.add_column(sa.Column("inputs_from", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("inputs_from")
        batch.drop_column("initiated_by")
    with op.batch_alter_table("work_items") as batch:
        batch.drop_column("initiated_by")
