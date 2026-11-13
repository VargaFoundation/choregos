# SPDX-License-Identifier: Apache-2.0
"""Groups that belong to the organisation: `org_groups`, `org_group_members` (S66-01, ADR 0152)

An organisation's groups, each with one source (`internal` or `scim`), and who is in each. Both tables
are under forced RLS like `machine_accounts`. The downgrade drops both tables and loses the groups and
their members; the identity provider's groups (`users.groups`) are not touched.

Revision ID: d4a6c8e0f2b5
Revises: b3dbe73fff38
Create Date: 2026-11-12 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a6c8e0f2b5"
down_revision: str | None = "b3dbe73fff38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("org_groups", "org_group_members")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "org_groups",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_org_groups_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_groups")),
        sa.UniqueConstraint("org_id", "slug", name="uq_org_groups_org_slug"),
    )
    op.create_index(op.f("ix_org_groups_org_id"), "org_groups", ["org_id"], unique=False)
    op.create_index(
        "uq_org_groups_org_external_id",
        "org_groups",
        ["org_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
        sqlite_where=sa.text("external_id IS NOT NULL"),
    )

    op.create_table(
        "org_group_members",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("group_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("added_by", sa.String(length=320), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_org_group_members_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["org_groups.id"],
            name=op.f("fk_org_group_members_group_id_org_groups"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_org_group_members_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_group_members")),
        sa.UniqueConstraint("group_id", "user_id", name="uq_org_group_members_group_user"),
    )
    op.create_index(op.f("ix_org_group_members_org_id"), "org_group_members", ["org_id"], unique=False)
    op.create_index(op.f("ix_org_group_members_group_id"), "org_group_members", ["group_id"], unique=False)
    op.create_index(op.f("ix_org_group_members_user_id"), "org_group_members", ["user_id"], unique=False)

    if _postgres():
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"""
                CREATE POLICY {table}_org_isolation ON {table}
                USING (
                  '*' = ANY(choregos_current_orgs())
                  OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
                )
                """
            )


def downgrade() -> None:
    if _postgres():
        for table in TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    op.drop_index(op.f("ix_org_group_members_user_id"), table_name="org_group_members")
    op.drop_index(op.f("ix_org_group_members_group_id"), table_name="org_group_members")
    op.drop_index(op.f("ix_org_group_members_org_id"), table_name="org_group_members")
    op.drop_table("org_group_members")
    op.drop_index("uq_org_groups_org_external_id", table_name="org_groups")
    op.drop_index(op.f("ix_org_groups_org_id"), table_name="org_groups")
    op.drop_table("org_groups")
