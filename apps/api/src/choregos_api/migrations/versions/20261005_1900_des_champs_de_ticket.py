# SPDX-License-Identifier: Apache-2.0
"""Des champs de ticket, validés par le JSON Schema que déclare leur workflow (ADR 0031)

Revision ID: c9e1a3b5d7f0
Revises: b8d0f2a4c6e9
Create Date: 2026-10-05 19:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "c9e1a3b5d7f0"
down_revision: str | None = "b8d0f2a4c6e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.add_column(sa.Column("fields", Json(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.drop_column("fields")
