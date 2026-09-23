"""L'identifiant d'un run est lisible, pas un UUID : 128 caractères

`<ticket>-<transition>-<tentative>` dépasse les 36 caractères d'un UUID. PostgreSQL
répondait « value too long for type character varying(36) » à la première étape ; SQLite,
qui n'applique pas les longueurs déclarées, ne disait rien — d'où un défaut invisible
partout sauf en vrai. Les colonnes qui référencent un run suivent.

Revision ID: 0df3d8ee7496
Revises: 7bdeea6732b0
Create Date: 2026-09-22 23:35:30.487643+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0df3d8ee7496'
down_revision: str | None = '7bdeea6732b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


COLONNES = [
    # (table, colonne, nullable)
    ("cost_ledger", "run_id", True),
    ("findings", "origin_run_id", True),
    ("gateway_keys", "run_id", True),
    ("run_events", "run_id", False),
    ("runs", "id", False),
]


def _changer(vers: int, depuis: int) -> None:
    # `batch_alter_table` et pas `alter_column` : SQLite ne sait pas faire
    # `ALTER COLUMN … TYPE`, et le test qui compare migrations et modèles tourne dessus.
    # Sur PostgreSQL, la forme « batch » retombe sur un ALTER ordinaire.
    for table, colonne, nullable in COLONNES:
        with op.batch_alter_table(table) as lot:
            lot.alter_column(
                colonne,
                existing_type=sa.String(length=depuis),
                type_=sa.String(length=vers),
                existing_nullable=nullable,
            )


def upgrade() -> None:
    _changer(vers=128, depuis=36)


def downgrade() -> None:
    _changer(vers=36, depuis=128)
