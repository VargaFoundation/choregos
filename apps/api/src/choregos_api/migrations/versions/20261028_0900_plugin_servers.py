# SPDX-License-Identifier: Apache-2.0
"""A plugin's server is an organisation connector (S43-06, ADR 0094): `org_connectors.plugin_server`

The installer writes the server's declaration there; the broker reads its operations as the
governance floor. Nullable, no backfill: no existing connector is a plugin's. The downgrade drops the
column: the connectors stay, governed as any MCP connector, and their servers keep running until the
plugin is uninstalled by a version that knows them.

Revision ID: c9e1a3b5d7f8
Revises: b8d0f2a4c6e7
Create Date: 2026-10-28 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "c9e1a3b5d7f8"
down_revision: str | None = "b8d0f2a4c6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("org_connectors") as batch:
        batch.add_column(sa.Column("plugin_server", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("org_connectors") as batch:
        batch.drop_column("plugin_server")
