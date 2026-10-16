# SPDX-License-Identifier: Apache-2.0
"""GitHub App installations: `github_installations` (S40-04, ADR 0073)

Installations of the deployment's GitHub App, discovered rather than typed, and claimed by a
Choregos organisation. Under forced RLS: a claimed row is readable by its organisation, an
unclaimed one only in the platform scope.

Revision ID: d4f6a8c0e2b4
Revises: b2c4d6e8f0a1
Create Date: 2026-10-16 11:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6a8c0e2b4"
down_revision: str | None = "b2c4d6e8f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "github_installations",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("account_login", sa.String(length=200), nullable=False),
        sa.Column("account_key", sa.String(length=200), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("account_type", sa.String(length=32), nullable=False),
        sa.Column("app_id", sa.String(length=32), nullable=False),
        sa.Column("repository_selection", sa.String(length=16), nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("org_id", sa.String(length=36), nullable=True),
        sa.Column("claimed_by", sa.String(length=320), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_github_installations_org_id_organizations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_installations")),
    )
    op.create_index(op.f("ix_github_installations_account_key"), "github_installations", ["account_key"], unique=False)
    op.create_index(op.f("ix_github_installations_org_id"), "github_installations", ["org_id"], unique=False)

    if _postgres():
        op.execute("ALTER TABLE github_installations ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE github_installations FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY github_installations_org_isolation ON github_installations
            USING (
              '*' = ANY(choregos_current_orgs())
              OR (org_id IS NOT NULL AND choregos_org_slug(org_id) = ANY(choregos_current_orgs()))
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS github_installations_org_isolation ON github_installations")
    op.drop_index(op.f("ix_github_installations_org_id"), table_name="github_installations")
    op.drop_index(op.f("ix_github_installations_account_key"), table_name="github_installations")
    op.drop_table("github_installations")
