# SPDX-License-Identifier: Apache-2.0
"""The GitHub App registered from a manifest: `github_app_registrations` (S67-31, ADR 0161)

A platform administrator creates the deployment's GitHub App from the console (the GitHub App Manifest
flow); GitHub answers its credentials once. What GitHub publishes about the App is kept in clear, its
private key, webhook secret and client secret in one envelope sealed under the deployment's key (ADR
0058). Under forced RLS, readable in the platform scope only: no organisation reads the instance's App.
A new table, nothing rewritten; the downgrade drops it (and the App's credentials with it).

Revision ID: 6dbee3522739
Revises: f9b1d3e5a7c0
Create Date: 2026-11-12 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6dbee3522739"
down_revision: str | None = "f9b1d3e5a7c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "github_app_registrations"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("app_id", sa.String(length=32), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("owner_login", sa.String(length=200), nullable=False),
        sa.Column("owner_type", sa.String(length=32), nullable=False),
        sa.Column("html_url", sa.String(length=300), nullable=False),
        sa.Column("public", sa.Boolean(), nullable=False),
        sa.Column("webhook_active", sa.Boolean(), nullable=False),
        sa.Column("client_id", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_dek", sa.LargeBinary(), nullable=False),
        sa.Column("dek_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("kek_id", sa.String(length=32), nullable=False),
        sa.Column("algorithm", sa.String(length=16), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_app_registrations")),
        sa.UniqueConstraint("app_id", name=op.f("uq_github_app_registrations_app_id")),
    )
    if _postgres():
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {TABLE}_platform_only ON {TABLE}
            USING ('*' = ANY(choregos_current_orgs()))
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_platform_only ON {TABLE}")
    op.drop_table(TABLE)
