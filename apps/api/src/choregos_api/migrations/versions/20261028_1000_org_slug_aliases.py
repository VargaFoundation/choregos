# SPDX-License-Identifier: Apache-2.0
"""Organisation slug aliases (S42-14, ADR 0090)

An organisation's id is its identity, its slug an address: renaming the slug keeps the old one in
`org_slug_aliases`, so old URLs, MCP and A2A addresses, qualified ids and IdP group names keep
resolving to the organisation. The table maps an address to an organisation id and holds no content: it is outside RLS, because
an address has to be resolved before any scope exists (ADR 0090).

Revision ID: f4b6d8a0c2e5
Revises: a8c0e2f4b6d9
Create Date: 2026-10-28 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4b6d8a0c2e5"
down_revision: str | None = "a8c0e2f4b6d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "org_slug_aliases",
        sa.Column("alias", sa.String(length=64), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=320), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_org_slug_aliases_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("alias", name=op.f("pk_org_slug_aliases")),
    )
    op.create_index(op.f("ix_org_slug_aliases_org_id"), "org_slug_aliases", ["org_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_org_slug_aliases_org_id"), table_name="org_slug_aliases")
    op.drop_table("org_slug_aliases")
