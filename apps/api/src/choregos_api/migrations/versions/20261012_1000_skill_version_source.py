# SPDX-License-Identifier: Apache-2.0
"""Where a skill version comes from (S32-05): `skill_versions.source`

A version imported from git records its repository, the commit SHA it is pinned to, the folder
and the licence found there. Nullable: a zip, the console or a template has no git source.

Revision ID: 4e6a8c0b2d3f
Revises: d5f7a9b1c3e4
Create Date: 2026-10-12 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "4e6a8c0b2d3f"
down_revision: str | None = "d5f7a9b1c3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("skill_versions") as batch:
        batch.add_column(sa.Column("source", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("skill_versions") as batch:
        batch.drop_column("source")
