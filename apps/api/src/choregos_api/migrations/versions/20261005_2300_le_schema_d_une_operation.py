# SPDX-License-Identifier: Apache-2.0
"""Le schéma d'entrée d'une opération, gardé (ADR 0034) : le courtier l'annonce à l'agent et
refuse un appel qui ne le tient pas, avant de joindre le serveur.

Revision ID: e7f9a1b3c5d8
Revises: d6e8f0a2b4c7
Create Date: 2026-10-05 23:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "e7f9a1b3c5d8"
down_revision: str | None = "d6e8f0a2b4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("connector_operations") as table:
        table.add_column(sa.Column("input_schema", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("connector_operations") as table:
        table.drop_column("input_schema")
