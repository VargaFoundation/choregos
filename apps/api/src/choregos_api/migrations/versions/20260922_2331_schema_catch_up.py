# SPDX-License-Identifier: Apache-2.0
"""Schema catch-up: what the models had and the migrations did not.

`runs.spend_collected` was added to the model without a migration. Nobody saw it because the
API laid the schema itself at start-up (`create_all`) in the `dev` environment, and the cluster
bench ran alembic with `|| true`. On a really migrated database, the first write of a state
comment failed: "column runs.spend_collected does not exist".

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
    # `server_default`: the table may already hold rows, and a NOT NULL column
    # without a default would make the migration fail on any non-empty database.
    op.add_column(
        'runs',
        sa.Column('spend_collected', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('runs', 'spend_collected')
