"""Un ticket mort se voit : la marque que l'interpréteur pose en mourant

Deux tickets RH du banc du 2026-09-24 sont restés « en attente » alors que leur workflow
Temporal était FAILED — l'un sur une activité en échec, l'autre sur un heartbeat manqué
pendant un `helm upgrade`. Rien en base ne le disait, donc rien à l'écran. Le workflow
écrit désormais ici pourquoi il meurt (`{message, activity, state, at}`), et `load_context`
efface la marque quand il redémarre.

Revision ID: a1c3e5f7b9d2
Revises: 668d35b9d531
Create Date: 2026-09-24 18:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = 'a1c3e5f7b9d2'
down_revision: str | None = '668d35b9d531'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('work_items', sa.Column('failure', Json(), nullable=True))


def downgrade() -> None:
    op.drop_column('work_items', 'failure')
