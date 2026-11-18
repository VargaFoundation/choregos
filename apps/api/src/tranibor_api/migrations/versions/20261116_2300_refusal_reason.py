# SPDX-License-Identifier: Apache-2.0
"""A refusal says why: `conversation_actions.refusal_reason` (S67-16b, ADR 0154 §7)

The person who refuses a write or a launch an assistant proposed gives a reason; it is kept on the action
(null for every row that exists, and for a refusal sent by a client that sends none). Nothing of the
policies moves: the owner-only RESTRICTIVE policy of `conversation_actions` covers the new column. The
downgrade drops the column: the reasons already given are lost (the audit records keep them).

Revision ID: c8a1e3b5d7f9
Revises: b7d2f4a6c8e1
Create Date: 2026-11-16 23:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8a1e3b5d7f9"
down_revision: str | None = "b7d2f4a6c8e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_actions") as batch:
        batch.add_column(sa.Column("refusal_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversation_actions") as batch:
        batch.drop_column("refusal_reason")
