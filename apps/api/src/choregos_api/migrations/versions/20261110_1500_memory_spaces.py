# SPDX-License-Identifier: Apache-2.0
"""Memory spaces of an installation, a person and a subject (S63-09, ADR 0136)

`memory_stores.scope` accepts `installation`, `user` and `user_subject`; the store gains `installation_id`,
`user_id`, `subject_type`, `subject_key`, `kind`, `schema`, `writes`, `retention_days` and `max_facts`, with
CHECK constraints that tie each scope to the columns it needs. A new table `memory_profiles` holds one row
per version of a profile document.

On PostgreSQL a RESTRICTIVE policy on `memory_stores` lets the platform scope (`*`) and the owner read a
`user` or `user_subject` store, and the facts and profiles of such a store follow it: what the first
(permissive, per organisation) policy allows is narrowed to the owner, as for a personal space. The
downgrade deletes the assistants' stores (a downgraded instance could not tell them from a global memory).

Revision ID: d9b1d3f5a7c9
Revises: d4e6f8a0c2e4
Create Date: 2026-11-10 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9b1d3f5a7c9"
down_revision: str | None = "d4e6f8a0c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STORES_OWNER = """
CREATE POLICY memory_stores_user_owner ON memory_stores AS RESTRICTIVE
USING (
  '*' = ANY(choregos_current_orgs())
  OR scope NOT IN ('user', 'user_subject')
  OR user_id = choregos_utilisateur_courant()
)
"""
FACTS_FOLLOW = """
CREATE POLICY memory_facts_user_owner ON memory_facts AS RESTRICTIVE
USING (
  memory_store_id IS NULL
  OR EXISTS (SELECT 1 FROM memory_stores s WHERE s.id = memory_facts.memory_store_id)
)
"""
PROFILES_FOLLOW = """
CREATE POLICY memory_profiles_user_owner ON memory_profiles AS RESTRICTIVE
USING (EXISTS (SELECT 1 FROM memory_stores s WHERE s.id = memory_profiles.memory_store_id))
"""
PROFILES_ORG = """
CREATE POLICY memory_profiles_org_isolation ON memory_profiles
USING (
  '*' = ANY(choregos_current_orgs())
  OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
)
"""
CHECKS = {
    "ck_memory_stores_installation": (
        "(scope IN ('installation', 'user', 'user_subject')) = (installation_id IS NOT NULL)"
    ),
    "ck_memory_stores_owner": "(scope IN ('user', 'user_subject')) = (user_id IS NOT NULL)",
    "ck_memory_stores_subject": (
        "(scope = 'user_subject') = (subject_type IS NOT NULL AND subject_key IS NOT NULL)"
    ),
    "ck_memory_stores_kind": "kind IN ('facts', 'profile')",
}


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("memory_stores") as batch:
        batch.add_column(sa.Column("installation_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("user_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("subject_type", sa.String(length=63), nullable=True))
        batch.add_column(sa.Column("subject_key", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("kind", sa.String(length=16), server_default="facts", nullable=False))
        batch.add_column(sa.Column("schema", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("writes", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("retention_days", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("max_facts", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            batch.f("fk_memory_stores_installation_id_assistant_installations"),
            "assistant_installations",
            ["installation_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            batch.f("fk_memory_stores_user_id_users"), "users", ["user_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index(batch.f("ix_memory_stores_installation_id"), ["installation_id"], unique=False)
        batch.create_index(batch.f("ix_memory_stores_user_id"), ["user_id"], unique=False)
        for name, expression in CHECKS.items():
            batch.create_check_constraint(batch.f(name), expression)
    op.create_table(
        "memory_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("memory_store_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("updated_by", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_memory_profiles_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["memory_store_id"],
            ["memory_stores.id"],
            name=op.f("fk_memory_profiles_memory_store_id_memory_stores"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_profiles")),
        sa.UniqueConstraint("memory_store_id", "version", name="uq_memory_profiles_version"),
    )
    op.create_index(op.f("ix_memory_profiles_org_id"), "memory_profiles", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_memory_profiles_memory_store_id"), "memory_profiles", ["memory_store_id"], unique=False
    )
    if _postgres():
        op.execute("ALTER TABLE memory_profiles ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE memory_profiles FORCE ROW LEVEL SECURITY")
        op.execute(PROFILES_ORG)
        op.execute(STORES_OWNER)
        op.execute(FACTS_FOLLOW)
        op.execute(PROFILES_FOLLOW)


def downgrade() -> None:
    if _postgres():
        # The deletion below sees every row, whatever the organisation, under forced row-level security.
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
        op.execute("DROP POLICY IF EXISTS memory_profiles_user_owner ON memory_profiles")
        op.execute("DROP POLICY IF EXISTS memory_facts_user_owner ON memory_facts")
        op.execute("DROP POLICY IF EXISTS memory_stores_user_owner ON memory_stores")
    op.drop_table("memory_profiles")
    op.execute("DELETE FROM memory_stores WHERE scope IN ('installation', 'user', 'user_subject')")
    with op.batch_alter_table("memory_stores") as batch:
        for name in CHECKS:
            batch.drop_constraint(batch.f(name), type_="check")
        batch.drop_index(batch.f("ix_memory_stores_user_id"))
        batch.drop_index(batch.f("ix_memory_stores_installation_id"))
        batch.drop_constraint(batch.f("fk_memory_stores_user_id_users"), type_="foreignkey")
        batch.drop_constraint(
            batch.f("fk_memory_stores_installation_id_assistant_installations"), type_="foreignkey"
        )
        for column in (
            "max_facts",
            "retention_days",
            "writes",
            "schema",
            "kind",
            "subject_key",
            "subject_type",
            "user_id",
            "installation_id",
        ):
            batch.drop_column(column)
