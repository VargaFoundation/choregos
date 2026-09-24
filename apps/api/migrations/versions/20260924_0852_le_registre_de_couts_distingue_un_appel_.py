"""Le registre de coûts distingue un appel de modèle d'un appel d'outil

Un outil du catalogue coûte à chaque appel, comme un modèle. Sans cette colonne, il
faudrait le ranger dans `model`, et tous les agrégats par modèle compteraient les outils
comme des modèles.

`server_default` n'est pas décoratif : l'autogénération produisait une colonne `NOT NULL`
sans défaut, ce que PostgreSQL refuse sur une table qui a déjà des lignes — « column
"kind" contains null values ». SQLite, lui, ne dit rien : le défaut aurait encore été
invisible partout sauf en vrai. Les lignes existantes sont toutes des appels de modèle.

Revision ID: 668d35b9d531
Revises: 0df3d8ee7496
Create Date: 2026-09-24 08:52:18.927703+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '668d35b9d531'
down_revision: str | None = '0df3d8ee7496'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'cost_ledger',
        sa.Column('kind', sa.String(length=16), nullable=False, server_default='model'),
    )
    op.create_index(op.f('ix_cost_ledger_kind'), 'cost_ledger', ['kind'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_cost_ledger_kind'), table_name='cost_ledger')
    op.drop_column('cost_ledger', 'kind')
