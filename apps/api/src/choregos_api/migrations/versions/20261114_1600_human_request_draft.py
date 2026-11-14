# SPDX-License-Identifier: Apache-2.0
"""A draft of a person's decision, deposited by their client (ADR 0163): `human_requests.draft`

A person's client prepares, through the MCP door, the decision of a request that waits for them. The draft
is kept on the request row, as JSON, one per request; the person takes it into the decision's form or
discards it. Null for every existing row.

A nullable column only: no row rewritten, and nothing of row-level security moves. The downgrade drops it.

Revision ID: 1596f0e2131d
Revises: dcdaeba28e08
Create Date: 2026-11-14 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "1596f0e2131d"
down_revision: str | None = "dcdaeba28e08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("human_requests") as batch:
        batch.add_column(sa.Column("draft", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("human_requests") as batch:
        batch.drop_column("draft")
