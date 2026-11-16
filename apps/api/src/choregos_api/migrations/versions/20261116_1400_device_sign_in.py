# SPDX-License-Identifier: Apache-2.0
"""A developer surface signs in as the person: device authorisations and surface sessions (ADR 0154 §6)

`user_sessions` gains `kind` (`browser` for every row that exists), `client_name`, `auth_time` and
`idle_expires_at`. `device_authorizations` holds the RFC 8628 codes' hashes while the person decides;
forced RLS, platform scope only. The downgrade drops the table and the columns; a surface session is
then no longer known and its credential is refused.

Revision ID: b7d2f4a6c8e1
Revises: 9a3c5e7b1d24
Create Date: 2026-11-16 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f4a6c8e1"
down_revision: str | None = "9a3c5e7b1d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WHOLE = "'*' = ANY(choregos_current_orgs())"


def upgrade() -> None:
    with op.batch_alter_table("user_sessions") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=16), server_default="browser", nullable=False))
        batch.add_column(sa.Column("client_name", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("auth_time", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "device_authorizations",
        sa.Column("device_hash", sa.String(length=64), nullable=False),
        sa.Column("user_code_hash", sa.String(length=64), nullable=False),
        sa.Column("client_kind", sa.String(length=16), nullable=False),
        sa.Column("client_name", sa.String(length=80), nullable=True),
        sa.Column("org_slug", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("auth_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_s", sa.Integer(), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
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
            ["user_id"],
            ["users.id"],
            name=op.f("fk_device_authorizations_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_authorizations")),
        sa.UniqueConstraint("device_hash", name=op.f("uq_device_authorizations_device_hash")),
        sa.UniqueConstraint("user_code_hash", name=op.f("uq_device_authorizations_user_code_hash")),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE device_authorizations ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE device_authorizations FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY device_authorizations_platform ON device_authorizations "
            f"USING ({WHOLE}) WITH CHECK ({WHOLE})"
        )


def downgrade() -> None:
    op.drop_table("device_authorizations")
    with op.batch_alter_table("user_sessions") as batch:
        batch.drop_column("idle_expires_at")
        batch.drop_column("auth_time")
        batch.drop_column("client_name")
        batch.drop_column("kind")
