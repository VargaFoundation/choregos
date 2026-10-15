# SPDX-License-Identifier: Apache-2.0
"""Knowledge distillation (S33-05): what the last distillation of a base gave

Revision ID: b9d1f3a5c7e8
Revises: f7b9d1e3a5c6
Create Date: 2026-10-13 11:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "b9d1f3a5c7e8"
down_revision: str | None = "f7b9d1e3a5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(sa.Column("distillation", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_column("distillation")
