# SPDX-License-Identifier: Apache-2.0
"""A stopped assistant: who stopped it, when and why (S64-09c, ADR 0142 §4 bis)

`assistant_installations.stopped_at`, `stopped_by` and `stopped_reason` are set by
`POST /orgs/{org}/assistants/{slug}/stop` and cleared by `.../resume`. While `stopped_at` is set, the
installation's derived status reads `suspended`, whatever its readiness or its plugin say, so that the
next derivation does not undo a stop.

Three nullable columns: no row is rewritten, and nothing of row-level security moves (the table's
policies are its organisation's). The cancel request a stop writes on each conversation in flight is the
column S63-23 added, `agent_sessions.turn_cancel_requested_at`. The downgrade drops the three columns,
and with them every stop: a downgraded instance derives its assistants' status again.

Revision ID: f1b3d5e7a9c2
Revises: f9b1d3e5a7c0
Create Date: 2026-11-12 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1b3d5e7a9c2"
down_revision: str | None = "f9b1d3e5a7c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assistant_installations") as batch:
        batch.add_column(sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("stopped_by", sa.String(length=320), nullable=True))
        batch.add_column(sa.Column("stopped_reason", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assistant_installations") as batch:
        batch.drop_column("stopped_reason")
        batch.drop_column("stopped_by")
        batch.drop_column("stopped_at")
