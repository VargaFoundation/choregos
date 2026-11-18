# SPDX-License-Identifier: Apache-2.0
"""The lifecycle of an agent (S32-04): `agents.lifecycle`, `agents.retired_at`

`draft` → `active` → `deprecated` → `retired`, kept apart from the operational `status` (active,
suspended, revoked). Every existing agent is `active`.

Revision ID: d6f8b0c2e4a6
Revises: 4e6a8c0b2d3f
Create Date: 2026-10-13 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6f8b0c2e4a6"
down_revision: str | None = "4e6a8c0b2d3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("lifecycle", sa.String(length=16), server_default="active", nullable=False))
        batch.add_column(sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("retired_at")
        batch.drop_column("lifecycle")
