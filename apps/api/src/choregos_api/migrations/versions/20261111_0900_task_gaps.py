# SPDX-License-Identifier: Apache-2.0
"""Closing the task gaps: reminder, webhook secret, proposal diff (S63-10, S63-13, S63-14)

`conversation_actions.diff`: what a source's preview of a proposed write returned (JSON), when it serves one.

`delegations.reminded_at`: when the person was told their delegation ends soon (once per delegation).
`assistant_installations.webhook_secret_generation` and `webhook_secret_shown_at`: the key of an installation's
task webhook is derived from the deployment's secret, the installation id and this generation; an owner is
shown it once, and a new generation (rotation) gives a new key. The downgrade drops the columns: a rotated
key goes back to generation 0.

Revision ID: a3e3c0f0e0d3
Revises: f4a6c8e0b2d4
Create Date: 2026-11-11 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "a3e3c0f0e0d3"
down_revision: str | None = "f4a6c8e0b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversation_actions") as batch:
        batch.add_column(sa.Column("diff", Json(), nullable=True))
    with op.batch_alter_table("delegations") as batch:
        batch.add_column(sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("assistant_installations") as batch:
        batch.add_column(
            sa.Column("webhook_secret_generation", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("webhook_secret_shown_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("conversation_actions") as batch:
        batch.drop_column("diff")
    with op.batch_alter_table("assistant_installations") as batch:
        batch.drop_column("webhook_secret_shown_at")
        batch.drop_column("webhook_secret_generation")
    with op.batch_alter_table("delegations") as batch:
        batch.drop_column("reminded_at")
