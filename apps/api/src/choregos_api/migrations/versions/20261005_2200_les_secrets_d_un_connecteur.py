# SPDX-License-Identifier: Apache-2.0
"""Les secrets d'un connecteur, en références, champ par champ (ADR 0034)

`connectors.secret_ref` (une seule référence, jamais lue) ne suffit pas : Jira a un jeton d'API
et un secret de webhook. `secret_refs` les nomme champ par champ ; aucune valeur n'y est stockée.

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
