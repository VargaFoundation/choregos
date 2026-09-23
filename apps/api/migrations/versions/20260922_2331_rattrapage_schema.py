"""Rattrapage du schéma : ce que les modèles avaient et les migrations pas.

`runs.spend_collected` a été ajoutée au modèle sans migration. Personne ne l'a vu parce que
l'API posait elle-même le schéma au démarrage (`create_all`) en environnement `dev`, et que
le banc sur cluster lançait alembic avec `|| true`. Sur une vraie base migrée, la première
écriture d'un commentaire d'état échouait : « column runs.spend_collected does not exist ».

Revision ID: 7bdeea6732b0
Revises: a6de8f2759b3
Create Date: 2026-09-22 23:31:13.540865+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '7bdeea6732b0'
down_revision: str | None = 'a6de8f2759b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `server_default` : la table peut déjà contenir des lignes, et une colonne NOT NULL
    # sans valeur par défaut ferait échouer la migration sur toute base non vide.
    op.add_column(
        'runs',
        sa.Column('spend_collected', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('runs', 'spend_collected')
