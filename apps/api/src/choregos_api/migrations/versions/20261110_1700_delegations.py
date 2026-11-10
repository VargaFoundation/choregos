# SPDX-License-Identifier: Apache-2.0
"""Standing delegations: `delegations` (S63-14, ADR 0137 points 3-4)

A person's read-only delegation of one task of an assistant: the needs and operations agreed to, the
expiry, the digest of the manifest's needs at agreement, and `secret_ref`, the name of the sealed offline
token (never a token column). Forced RLS on the organisation plus a RESTRICTIVE owner-only policy: only
the platform scope and the person read a row. The downgrade drops the table: every delegation is lost (a
person agrees again); sealed offline tokens stay in `org_secrets` until their organisation is deleted.

Revision ID: f4a6c8e0b2d4
Revises: e8c0e2a4b6d8
Create Date: 2026-11-10 17:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "f4a6c8e0b2d4"
down_revision: str | None = "e8c0e2a4b6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "delegations"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("task", sa.String(length=63), nullable=False),
        sa.Column("needs", Json(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("secret_ref", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("suspended_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_delegations_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["assistant_installations.id"],
            name=op.f("fk_delegations_installation_id_assistant_installations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_delegations_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delegations")),
    )
    op.create_index(op.f("ix_delegations_org_id"), TABLE, ["org_id"], unique=False)
    op.create_index(op.f("ix_delegations_installation_id"), TABLE, ["installation_id"], unique=False)
    op.create_index(op.f("ix_delegations_user_id"), TABLE, ["user_id"], unique=False)
    op.create_index("ix_delegations_member_task", TABLE, ["installation_id", "user_id", "task"], unique=False)
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
    op.drop_index("ix_delegations_member_task", table_name=TABLE)
    op.drop_index(op.f("ix_delegations_user_id"), table_name=TABLE)
    op.drop_index(op.f("ix_delegations_installation_id"), table_name=TABLE)
    op.drop_index(op.f("ix_delegations_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
