# SPDX-License-Identifier: Apache-2.0
"""The builds of hosted agents' zips: `hosted_builds` (S41-08, ADR 0080 §9)

One row per organisation and build key: every version with the same inputs shares the build, and
the versions stay as they were published. Under forced RLS, like every organisation's table.

Revision ID: e6a8c0f2b4d7
Revises: e4a6c8f0b2d5
Create Date: 2026-10-20 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6a8c0f2b4d7"
down_revision: str | None = "e4a6c8f0b2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "hosted_builds",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("image", sa.String(length=400), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_hosted_builds_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hosted_builds")),
        sa.UniqueConstraint("org_id", "key", name="uq_hosted_builds_org_key"),
    )
    op.create_index(op.f("ix_hosted_builds_org_id"), "hosted_builds", ["org_id"], unique=False)

    if _postgres():
        op.execute("ALTER TABLE hosted_builds ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE hosted_builds FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY hosted_builds_org_isolation ON hosted_builds
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS hosted_builds_org_isolation ON hosted_builds")
    op.drop_index(op.f("ix_hosted_builds_org_id"), table_name="hosted_builds")
    op.drop_table("hosted_builds")
