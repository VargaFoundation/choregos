# SPDX-License-Identifier: Apache-2.0
"""What an organisation's rule says of how a write at an outpost is delivered (S65-14, ADR 0149 §5-6)

A write through an outpost is delivered at most once, and may end in doubt. It is opened only when the
operation's rule says one of two things, and both are said on the operation's own row:

- `connector_operations.platform_arguments`: the arguments the platform fills on a governed call, as
  the organisation states them for a discovered tool. Listing `action_key` says the operation takes
  the platform's idempotency key. Null on every existing row: nothing is filled that was not.
- `connector_operations.in_doubt_accepted`: an in-doubt outcome is accepted for the operation,
  explicitly. False on every existing row: no write through an outpost could be opened before this
  revision, so none is open under a statement nobody made.

`actions.status` and `action_effects.status` are plain strings with no check constraint: the new
`in_doubt` needs no change of theirs. The downgrade drops both columns; a write opened under either
statement is then an open write with neither, which the code of the earlier revision never delivers
(it places no write through an outpost at all).

Revision ID: b3dbe73fff38
Revises: f9b1d3e5a7c0
Create Date: 2026-11-12 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "b3dbe73fff38"
down_revision: str | None = "f9b1d3e5a7c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("connector_operations") as table:
        table.add_column(sa.Column("platform_arguments", Json(), nullable=True))
        table.add_column(
            sa.Column("in_doubt_accepted", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("connector_operations") as table:
        table.drop_column("in_doubt_accepted")
        table.drop_column("platform_arguments")
