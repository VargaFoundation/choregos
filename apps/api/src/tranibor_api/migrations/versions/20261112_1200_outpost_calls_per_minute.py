# SPDX-License-Identifier: Apache-2.0
"""An outpost's calls of the last minute, read through an index (S65-13, ADR 0148 §10)

Calls per minute are capped per outpost and per organisation where a call is admitted. The count is
read from the calls' own rows, so that every replica agrees on it: an outpost's calls placed in the
last sixty seconds. `outpost_calls` keeps a row per call for good (the digests stay, ADR 0148 §11),
and the one index that leads with the outpost, `(outpost_id, state)`, would have every placement walk
all the calls the outpost ever took. `(outpost_id, created_at)` makes it the last minute's alone.

An index only: no column, no row rewritten, and nothing of row-level security moves (the policies of
`20261112_1000_outposts` are the table's). The downgrade drops it.

Revision ID: e8a0c2d4f6b9
Revises: d7f9b1c3e5a8
Create Date: 2026-11-12 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e8a0c2d4f6b9"
down_revision: str | None = "d7f9b1c3e5a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_outpost_calls_outpost_created"


def upgrade() -> None:
    op.create_index(INDEX, "outpost_calls", ["outpost_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(INDEX, table_name="outpost_calls")
