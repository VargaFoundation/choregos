# SPDX-License-Identifier: Apache-2.0
"""The cost ledger tells a model call from a tool call

A catalogue tool costs on every call, like a model. Without this column, it would have to
be filed under `model`, and every per-model aggregate would count the tools as models.

`server_default` is not decorative: autogenerate produced a `NOT NULL` column without a
default, which PostgreSQL refuses on a table that already has rows: "column
"kind" contains null values". SQLite says nothing: the default would again have been
invisible everywhere except in real life. The existing rows are all model calls.

Revision ID: 668d35b9d531
Revises: 0df3d8ee7496
Create Date: 2026-09-24 08:52:18.927703+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '668d35b9d531'
down_revision: str | None = '0df3d8ee7496'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'cost_ledger',
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='model'),
    )
    op.create_index(op.f('ix_cost_ledger_kind'), 'cost_ledger', ['kind'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_cost_ledger_kind'), table_name='cost_ledger')
    op.drop_column('cost_ledger', 'kind')
