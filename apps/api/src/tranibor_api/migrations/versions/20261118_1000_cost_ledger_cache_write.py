# SPDX-License-Identifier: Apache-2.0
"""The cost ledger separates cache writes from cache reads (S4-07, ADR 0188)

`cost_ledger.tokens_cached` stays the input tokens read from the provider's cache. The new
`tokens_cache_write` holds the ones written to it (Anthropic's cache creation, dearer than a fresh
token). A line booked before this migration read the gateway without the writes: it gets 0, which
means "not told", not "none".

Revision ID: f3c5a7e9b1d2
Revises: e2a4c6b8d0f3
Create Date: 2026-11-18 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3c5a7e9b1d2"
down_revision: str | None = "e2a4c6b8d0f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cost_ledger",
        sa.Column("tokens_cache_write", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("cost_ledger", "tokens_cache_write")
