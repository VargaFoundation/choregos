# SPDX-License-Identifier: Apache-2.0
"""A dead work item is visible: the mark the interpreter leaves when dying

Two HR tickets of the 2026-09-24 bench stayed "waiting" while their Temporal workflow
was FAILED: one on a failed activity, the other on a missed heartbeat
during a `helm upgrade`. Nothing in the database said so, hence nothing on screen. The workflow
now writes here why it dies (`{message, activity, state, at}`), and `load_context`
clears the mark when it restarts.

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
