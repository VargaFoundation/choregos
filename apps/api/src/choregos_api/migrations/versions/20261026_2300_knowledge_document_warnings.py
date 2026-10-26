# SPDX-License-Identifier: Apache-2.0
"""A knowledge document says what its conversion left out (S42-09e): `knowledge_documents.warnings`,
a list of sentences, empty by default (a spreadsheet read up to its row cap names each cut sheet).

Revision ID: a7c9e1b3d5f6
Revises: e3a5c7b9d1f4
Create Date: 2026-10-26 23:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "a7c9e1b3d5f6"
down_revision: str | None = "e3a5c7b9d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.add_column(sa.Column("warnings", Json(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("knowledge_documents") as batch:
        batch.drop_column("warnings")
