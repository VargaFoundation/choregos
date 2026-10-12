# SPDX-License-Identifier: Apache-2.0
"""The supply-chain gate of a skill version (S32-06): risk, findings, review

`risk` and `findings` stay empty for the versions published before the gate (not assessed);
`review_status` is `approved` for them — they were published by an administrator, as the only
way in was then.

Revision ID: c5e7a9b1d3f5
Revises: b4d6f8a0c2e4
Create Date: 2026-10-12 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "c5e7a9b1d3f5"
down_revision: str | None = "b4d6f8a0c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("skill_versions") as batch:
        batch.add_column(sa.Column("risk", sa.String(length=8), nullable=True))
        batch.add_column(sa.Column("findings", Json(), nullable=True))
        batch.add_column(
            sa.Column("review_status", sa.String(length=16), server_default="approved", nullable=False)
        )
        batch.add_column(sa.Column("reviewed_by", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("skill_versions") as batch:
        for colonne in ("reviewed_at", "reviewed_by", "review_status", "findings", "risk"):
            batch.drop_column(colonne)
