# SPDX-License-Identifier: Apache-2.0
"""The most a voice session of a model may spend per minute: `model_catalogue.max_usd_per_minute`
(S41-15, ADR 0080 §10)

Null for every existing row: no rate, the session's reservation alone caps it.

Revision ID: 3c5e7a9b1d2f
Revises: 97b19c72c16e
Create Date: 2026-10-20 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c5e7a9b1d2f"
down_revision: str | None = "97b19c72c16e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_catalogue") as batch:
        batch.add_column(sa.Column("max_usd_per_minute", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_catalogue") as batch:
        batch.drop_column("max_usd_per_minute")
