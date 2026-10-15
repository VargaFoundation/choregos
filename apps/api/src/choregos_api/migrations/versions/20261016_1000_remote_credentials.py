# SPDX-License-Identifier: Apache-2.0
"""Remote agent credentials: `remote_credentials` (ADR 0062)

The organisation's way into remote agents reached over A2A: an OAuth registration (the refresh token
itself is a sealed value of `org_secrets`), a bearer token reference, or nothing. Under forced RLS like
the rest of the registry.

Revision ID: a4c6e8b0d2f4
Revises: f1b3d5a7c9e2
Create Date: 2026-10-16 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4c6e8b0d2f4"
down_revision: str | None = "f1b3d5a7c9e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "remote_credentials",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("secret_ref", sa.String(length=240), nullable=True),
        sa.Column("authorised_as", sa.String(length=320), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_remote_credentials_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_remote_credentials")),
        sa.UniqueConstraint("org_id", "name", name="uq_remote_credentials_org_name"),
    )
    op.create_index(op.f("ix_remote_credentials_org_id"), "remote_credentials", ["org_id"], unique=False)

    if _postgres():
        op.execute("ALTER TABLE remote_credentials ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE remote_credentials FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY remote_credentials_org_isolation ON remote_credentials
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS remote_credentials_org_isolation ON remote_credentials")
    op.drop_index(op.f("ix_remote_credentials_org_id"), table_name="remote_credentials")
    op.drop_table("remote_credentials")
