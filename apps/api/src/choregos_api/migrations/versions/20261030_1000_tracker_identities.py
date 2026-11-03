# SPDX-License-Identifier: Apache-2.0
"""Tracker identities: `tracker_identities` (S47-14, ADR 0102)

A member's account on a tracker, by the provider's stable numeric user id, linked by an organisation
administrator. Under forced RLS like the rest of the organisation's rows. The downgrade drops the
table and every link in it.

Revision ID: e4a6c8d0f2b5
Revises: d5b7c9e1f3a4
Create Date: 2026-10-30 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e4a6c8d0f2b5"
down_revision: str | None = "d5b7c9e1f3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "tracker_identities"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("tracker", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=64), nullable=False),
        sa.Column("login", sa.String(length=255), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=True),
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
            name=op.f("fk_tracker_identities_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tracker_identities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tracker_identities")),
        sa.UniqueConstraint("org_id", "tracker", "external_id", name="uq_tracker_identities_external"),
        sa.UniqueConstraint("org_id", "user_id", "tracker", name="uq_tracker_identities_member"),
    )
    for column in ("org_id", "user_id"):
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column], unique=False)

    if _postgres():
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {TABLE}_org_isolation ON {TABLE}
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    for column in ("user_id", "org_id"):
        op.drop_index(op.f(f"ix_{TABLE}_{column}"), table_name=TABLE)
    op.drop_table(TABLE)
