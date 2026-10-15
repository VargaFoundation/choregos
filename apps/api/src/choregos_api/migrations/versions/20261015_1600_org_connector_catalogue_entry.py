# SPDX-License-Identifier: Apache-2.0
"""An organisation connector remembers the MCP catalogue entry it was installed from (S39-10c).

`org_connectors.catalogue_entry` holds `<id>@<catalogue_version>`, `NULL` for a connector declared
by hand. The catalogue's per-organisation state (installed, update available) is read from it.

Revision ID: c4e6a8b0d2f4
Revises: e7a9c1b3d5f7
Create Date: 2026-10-15 16:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4e6a8b0d2f4"
down_revision: str | None = "e7a9c1b3d5f7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("org_connectors", sa.Column("catalogue_entry", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("org_connectors", "catalogue_entry")
