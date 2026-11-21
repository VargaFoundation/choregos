# SPDX-License-Identifier: Apache-2.0
"""A cache write is told by its TTL and a catalogue entry carries its cache multipliers (S4-12, ADR 0200)

`cost_ledger.tokens_cache_write_1h` is the part of `tokens_cache_write` written with the 1-hour TTL.
A line booked before this migration gets 0, which reads as "5-minute or not told"; nothing is
backfilled. `model_catalogue` gets the three nullable multipliers (cache read, 5-minute write, 1-hour
write): null keeps the default of the model's provider family.

Revision ID: 5c1e9d3a7b42
Revises: b4d6f8a0c2e5
Create Date: 2026-11-20 13:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e9d3a7b42"
down_revision: str | None = "b4d6f8a0c2e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MULTIPLIERS = ("cache_read_multiplier", "cache_write_5m_multiplier", "cache_write_1h_multiplier")


def upgrade() -> None:
    op.add_column(
        "cost_ledger",
        sa.Column("tokens_cache_write_1h", sa.Integer(), nullable=False, server_default="0"),
    )
    for name in _MULTIPLIERS:
        op.add_column("model_catalogue", sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for name in reversed(_MULTIPLIERS):
        op.drop_column("model_catalogue", name)
    op.drop_column("cost_ledger", "tokens_cache_write_1h")
