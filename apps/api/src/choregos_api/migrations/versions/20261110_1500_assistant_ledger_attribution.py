"""The cost ledger names the installation and the person of a conversation's turn (S63-15, ADR 0133)

The person's month and the installation's month are summed from the ledger. A conversation's lines were
found through `session_id`, but deleting a conversation detaches its lines (`SET NULL`: the money was
spent), and a cap that a delete resets is not a cap. The two columns stay on the line.

Revision ID: d1f3a5b7c9e1
Revises: c4b6d8f0a2e3
Create Date: 2026-11-10 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1f3a5b7c9e1"
down_revision: str | None = "c4b6d8f0a2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("cost_ledger") as batch:
        batch.add_column(sa.Column("installation_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("user_id", sa.String(length=36), nullable=True))
    op.create_index("ix_cost_ledger_installation_id", "cost_ledger", ["installation_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_cost_ledger_installation_id", table_name="cost_ledger")
    with op.batch_alter_table("cost_ledger") as batch:
        batch.drop_column("user_id")
        batch.drop_column("installation_id")
