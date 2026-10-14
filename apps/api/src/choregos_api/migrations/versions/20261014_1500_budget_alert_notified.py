# SPDX-License-Identifier: Apache-2.0
"""A budget alert remembers when it reached Slack (S27-09): `budget_alerts.notified_at`

Revision ID: 4a6c8e0b2d3f
Revises: a9c1e3f5b7d9, 9e41c7a0d35b
Create Date: 2026-10-14 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4a6c8e0b2d3f"
down_revision: str | Sequence[str] | None = ("a9c1e3f5b7d9", "9e41c7a0d35b")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("budget_alerts") as batch:
        batch.add_column(sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("budget_alerts") as batch:
        batch.drop_column("notified_at")
