# SPDX-License-Identifier: Apache-2.0
"""Drop `executors` (S42-07d, finding #945)

`PUT /platform/executors` is gone (S42-07b) and `GET /platform/executors` answers from the
deployment's environment, so nothing reads or writes the table any more. It was an instance
catalogue outside RLS, with a unique index on `kind`. The downgrade recreates it, empty: the rows
it held (written by the removed PUT, read by nothing) are not restored.

Revision ID: e3a5c7b9d1f4
Revises: d9f1b3c5e7a2
Create Date: 2026-10-24 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a5c7b9d1f4"
down_revision: str | None = "d9f1b3c5e7a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_executors_kind"), table_name="executors")
    op.drop_table("executors")


def downgrade() -> None:
    op.create_table(
        "executors",
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cluster", sa.String(length=128), nullable=True),
        sa.Column("namespace_pattern", sa.String(length=128), nullable=True),
        sa.Column("runner_image", sa.String(length=255), nullable=True),
        sa.Column("default_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_executors")),
    )
    op.create_index(op.f("ix_executors_kind"), "executors", ["kind"], unique=True)
