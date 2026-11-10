# SPDX-License-Identifier: Apache-2.0
"""Connected accounts: `connected_accounts` (S63-08, ADR 0135 points 1, 3, 4, 6)

A person's account at a source that answers in their name (`user_consent`). Forced RLS on the
organisation, plus a RESTRICTIVE owner-only policy (like `projects_personal_owner`): only the platform
scope and the person read a row. The refresh token is not a column, `secret_ref` names its sealed secret.
The downgrade drops the table: every person's consent is lost (they connect again); the sealed secrets
stay in `org_secrets` until their organisation is deleted.

Revision ID: e8c0e2a4b6d8
Revises: d9b1d3f5a7c9
Create Date: 2026-11-10 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "e8c0e2a4b6d8"
down_revision: str | None = "d9b1d3f5a7c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "connected_accounts"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), server_default="user_consent", nullable=False),
        sa.Column("secret_ref", sa.String(length=300), nullable=False),
        sa.Column("scopes", Json(), nullable=False),
        sa.Column("subject", sa.String(length=320), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="connected", nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_connected_accounts_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_connected_accounts_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connected_accounts")),
        sa.UniqueConstraint("org_id", "user_id", "connector", name="uq_connected_accounts_member"),
    )
    op.create_index(op.f("ix_connected_accounts_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_connected_accounts_user_id"), TABLE, ["user_id"], unique=False)
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
        op.execute(
            f"""
            CREATE POLICY {TABLE}_owner_only ON {TABLE} AS RESTRICTIVE
            USING (
              '*' = ANY(choregos_current_orgs())
              OR user_id = choregos_utilisateur_courant()
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_owner_only ON {TABLE}")
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    op.drop_index(op.f("ix_connected_accounts_user_id"), table_name=TABLE)
    op.drop_index(op.f("ix_connected_accounts_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
