# SPDX-License-Identifier: Apache-2.0
"""A tool grant may bind its arguments (S60-06, ADR 0128)

`connector_operations.arguments`: a JSON Schema the arguments of every call of the operation must
satisfy, the organisation's rule. `project_operation_policies.arguments`: what a project binds on top.
Both null on every existing row: no call is constrained that was not. The downgrade drops both.

Revision ID: a6c8e0f2b4d7
Revises: c1a3e5b7d9f2
Create Date: 2026-11-09 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "a6c8e0f2b4d7"
down_revision: str | None = "c1a3e5b7d9f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("connector_operations") as table:
        table.add_column(sa.Column("arguments", Json(), nullable=True))
    with op.batch_alter_table("project_operation_policies") as table:
        table.add_column(sa.Column("arguments", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("project_operation_policies") as table:
        table.drop_column("arguments")
    with op.batch_alter_table("connector_operations") as table:
        table.drop_column("arguments")
