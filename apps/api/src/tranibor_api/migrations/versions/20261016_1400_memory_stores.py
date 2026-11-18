# SPDX-License-Identifier: Apache-2.0
"""Named memories, global and project (S40-11, ADR 0075): `memory_stores`, and `memory_facts` that
carry their organisation and their store

`memory_facts.org_id` is backfilled from the project, then NOT NULL; `project_id` becomes nullable
(a global memory's facts belong to no project) under a CHECK that a fact has a home; on PostgreSQL the
row-level-security policy keys on `org_id` from now on.

Revision ID: f2a4c6e8b0d1
Revises: e6b8d0f2a4c5
Create Date: 2026-10-16 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a4c6e8b0d1"
down_revision: str | None = "e6b8d0f2a4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STORES = "memory_stores"
FACTS = "memory_facts"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _policy(table: str, visible: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_org_isolation ON {table}
        USING (
          '*' = ANY(choregos_current_orgs())
          OR {visible} = ANY(choregos_current_orgs())
        )
        """
    )


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # The migration job runs as the application role, under forced RLS: without this, every
        # data step below sees zero rows (S39-27).
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
    op.create_table(
        STORES,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("owner_key", sa.String(length=36), nullable=False),
        sa.Column("reach", sa.String(length=16), nullable=True),
        sa.Column("projects", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.CheckConstraint("(scope = 'project') = (project_id IS NOT NULL)", name="ck_memory_stores_scope"),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_memory_stores_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_memory_stores_project_id_projects"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_stores")),
        sa.UniqueConstraint("org_id", "owner_key", "slug", name="uq_memory_stores_owner_slug"),
    )
    op.create_index(op.f("ix_memory_stores_org_id"), STORES, ["org_id"], unique=False)
    op.create_index(op.f("ix_memory_stores_project_id"), STORES, ["project_id"], unique=False)
    if _postgres():
        op.execute(f"ALTER TABLE {STORES} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {STORES} FORCE ROW LEVEL SECURITY")
        _policy(STORES, "choregos_org_slug(org_id)")

    with op.batch_alter_table(FACTS) as batch:
        batch.add_column(sa.Column("org_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("memory_store_id", sa.String(length=36), nullable=True))
    # A fact was always a project's: its organisation is the project's.
    op.execute(
        f"UPDATE {FACTS} SET org_id = (SELECT projects.org_id FROM projects WHERE projects.id = {FACTS}.project_id)"
    )
    with op.batch_alter_table(FACTS) as batch:
        batch.alter_column("org_id", existing_type=sa.String(length=36), nullable=False)
        batch.alter_column("project_id", existing_type=sa.String(length=36), nullable=True)
        batch.create_foreign_key(
            op.f("fk_memory_facts_org_id_organizations"), "organizations", ["org_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_foreign_key(
            op.f("fk_memory_facts_memory_store_id_memory_stores"),
            STORES,
            ["memory_store_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index(op.f("ix_memory_facts_org_id"), ["org_id"], unique=False)
        batch.create_index(op.f("ix_memory_facts_memory_store_id"), ["memory_store_id"], unique=False)
        batch.create_check_constraint(
            "ck_memory_facts_has_a_home", "project_id IS NOT NULL OR memory_store_id IS NOT NULL"
        )
    if _postgres():
        _policy(FACTS, "choregos_org_slug(org_id)")


def downgrade() -> None:
    bind = op.get_bind()
    orphans = bind.execute(sa.text(f"SELECT count(*) FROM {FACTS} WHERE project_id IS NULL")).scalar_one()
    if orphans:
        raise RuntimeError(
            f"cannot downgrade: {orphans} fact(s) belong to a global memory and to no project; "
            "delete the global memories (and their facts) first"
        )
    if _postgres():
        _policy(FACTS, "choregos_project_org(project_id)")
    with op.batch_alter_table(FACTS) as batch:
        batch.drop_constraint("ck_memory_facts_has_a_home", type_="check")
        batch.drop_index(op.f("ix_memory_facts_memory_store_id"))
        batch.drop_index(op.f("ix_memory_facts_org_id"))
        batch.drop_constraint(op.f("fk_memory_facts_memory_store_id_memory_stores"), type_="foreignkey")
        batch.drop_constraint(op.f("fk_memory_facts_org_id_organizations"), type_="foreignkey")
        batch.alter_column("project_id", existing_type=sa.String(length=36), nullable=False)
        batch.drop_column("memory_store_id")
        batch.drop_column("org_id")
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {STORES}_org_isolation ON {STORES}")
    op.drop_index(op.f("ix_memory_stores_project_id"), table_name=STORES)
    op.drop_index(op.f("ix_memory_stores_org_id"), table_name=STORES)
    op.drop_table(STORES)
