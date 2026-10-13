# SPDX-License-Identifier: Apache-2.0
"""Approval by a named group (ADR 0052, #401): `users.groups`, the identity provider's groups as of
the last sign-in through it, and `connector_operations.approver_group`, the group whose members
approve the actions an operation proposes.

Revision ID: 3d5f7b9c1e2a
Revises: a3c5e7f9b1d2
Create Date: 2026-10-13 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "3d5f7b9c1e2a"
down_revision: str | None = "a3c5e7f9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as table:
        table.add_column(sa.Column("groups", Json(), nullable=False, server_default="[]"))
    with op.batch_alter_table("connector_operations") as table:
        table.add_column(sa.Column("approver_group", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("connector_operations") as table:
        table.drop_column("approver_group")
    with op.batch_alter_table("users") as table:
        table.drop_column("groups")
