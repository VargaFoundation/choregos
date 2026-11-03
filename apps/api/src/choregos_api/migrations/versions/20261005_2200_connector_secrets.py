# SPDX-License-Identifier: Apache-2.0
"""A connector's secrets, as references, field by field (ADR 0034)

`connectors.secret_ref` (a single reference, never read) is not enough: Jira has an API token
and a webhook secret. `secret_refs` names them field by field; no value is stored in it.

Revision ID: c5d7e9f1a3b6
Revises: b4c6d8e0f2a5
Create Date: 2026-10-05 22:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "c5d7e9f1a3b6"
down_revision: str | None = "b4c6d8e0f2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("connectors") as table:
        table.add_column(sa.Column("secret_refs", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("connectors") as table:
        table.drop_column("secret_refs")
