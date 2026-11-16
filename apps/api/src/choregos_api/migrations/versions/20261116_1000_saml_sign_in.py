# SPDX-License-Identifier: Apache-2.0
"""SAML sign-in: `org_saml_idps` and `saml_consumed_assertions` (S70-24, ADR 0170)

One SAML identity provider per organisation, and the ids of the assertions already consumed (replay
protection). `org_saml_idps` is under forced RLS with the organisation policy; the assertions table
belongs to no organisation and is reachable in the platform scope only. New tables, nothing rewritten;
the downgrade drops them (and the providers configured since).

Revision ID: 9a3c5e7b1d24
Revises: 6dbee3522739
Create Date: 2026-11-16 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "9a3c5e7b1d24"
down_revision: str | None = "6dbee3522739"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "org_saml_idps",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("idp_entity_id", sa.String(length=500), nullable=False),
        sa.Column("sso_url", sa.String(length=1000), nullable=False),
        sa.Column("certificate_pem", sa.Text(), nullable=False),
        sa.Column("email_attribute", sa.String(length=200), nullable=True),
        sa.Column("groups_attribute", sa.String(length=200), nullable=True),
        sa.Column("group_map", Json(), nullable=False),
        sa.Column("domains", Json(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_org_saml_idps_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_saml_idps")),
        sa.UniqueConstraint("org_id", name="uq_org_saml_idps_org_id"),
    )
    op.create_index(op.f("ix_org_saml_idps_org_id"), "org_saml_idps", ["org_id"], unique=False)
    op.create_table(
        "saml_consumed_assertions",
        sa.Column("id", sa.String(length=200), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saml_consumed_assertions")),
    )
    if _postgres():
        op.execute("ALTER TABLE org_saml_idps ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE org_saml_idps FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY org_saml_idps_org_isolation ON org_saml_idps
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )
        op.execute("ALTER TABLE saml_consumed_assertions ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE saml_consumed_assertions FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY saml_consumed_assertions_platform_only ON saml_consumed_assertions
            USING ('*' = ANY(choregos_current_orgs()))
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS saml_consumed_assertions_platform_only ON saml_consumed_assertions")
        op.execute("DROP POLICY IF EXISTS org_saml_idps_org_isolation ON org_saml_idps")
    op.drop_table("saml_consumed_assertions")
    op.drop_index(op.f("ix_org_saml_idps_org_id"), table_name="org_saml_idps")
    op.drop_table("org_saml_idps")
