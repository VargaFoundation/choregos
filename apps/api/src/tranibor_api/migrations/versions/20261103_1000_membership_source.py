# SPDX-License-Identifier: Apache-2.0
"""Memberships record where they came from (S47-05, ADR 0100)

`memberships.source`: `idp` when an IdP sign-in created the row from the provider's groups,
`manual` otherwise (administrator, bootstrap, project creator). Every existing row becomes
`manual`: roles withdrawn at the IdP before this release are not revoked. The downgrade drops the
column.

Revision ID: d2f4a6c8e0b3
Revises: c6e8a0b2d4f1
Create Date: 2026-11-03 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2f4a6c8e0b3"
down_revision: str | None = "c6e8a0b2d4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "memberships",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
    )


def downgrade() -> None:
    op.drop_column("memberships", "source")
