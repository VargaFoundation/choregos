# SPDX-License-Identifier: Apache-2.0
"""The top-ups a person grants a work item's budget (S27-04, ADR 0047): `work_items.budget_top_up_usd`

Replaying a work item whose budget is spent needs a stated top-up; it adds to the policy's ticket
budget, and the audit log records who granted it.

Revision ID: 1b3d5f7a9c0e
Revises: 9e41c7a0d35b
Create Date: 2026-10-13 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1b3d5f7a9c0e"
down_revision: str | None = "9e41c7a0d35b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.add_column(
            sa.Column("budget_top_up_usd", sa.Float(), nullable=False, server_default=sa.text("0"))
        )


def downgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        batch.drop_column("budget_top_up_usd")
