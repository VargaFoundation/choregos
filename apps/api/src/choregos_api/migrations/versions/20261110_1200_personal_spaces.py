# SPDX-License-Identifier: Apache-2.0
"""Personal spaces: `projects.kind` and `projects.owner_user_id` (S63-03, ADR 0134)

A personal space is a project of kind `personal`, one per person and organisation, read by its owner only.
Every existing row is `standard`. The organisation's RLS policy on `projects` is permissive; the owner's
rule is a second, RESTRICTIVE policy, so it narrows what the first allows instead of widening it: only the
platform scope (`*`) and the owner read a personal space. Tables keyed on a project resolve its
organisation through `choregos_project_org`, which reads `projects` under RLS, so what a personal space
holds is hidden from another person as well.

The downgrade drops the policy, the index, the constraint and the columns, and with them every personal
space's kind: a downgraded instance would list them as ordinary projects, so it deletes them first.

Revision ID: b1d3f5a7c9e2
Revises: a7c9e1b3d5f7
Create Date: 2026-11-10 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1d3f5a7c9e2"
down_revision: str | None = "a7c9e1b3d5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_ONLY = """
CREATE POLICY projects_personal_owner ON projects AS RESTRICTIVE
USING (
  '*' = ANY(choregos_current_orgs())
  OR kind <> 'personal'
  OR owner_user_id = choregos_utilisateur_courant()
)
"""


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=16), server_default="standard", nullable=False))
        batch.add_column(sa.Column("owner_user_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            batch.f("fk_projects_owner_user_id_users"), "users", ["owner_user_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index(batch.f("ix_projects_owner_user_id"), ["owner_user_id"], unique=False)
        batch.create_check_constraint(
            batch.f("ck_projects_personal_has_owner"), "(kind = 'personal') = (owner_user_id IS NOT NULL)"
        )
    op.create_index(
        "uq_projects_personal_owner",
        "projects",
        ["org_id", "owner_user_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'personal'"),
        sqlite_where=sa.text("kind = 'personal'"),
    )
    if _postgres():
        op.execute(OWNER_ONLY)


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS projects_personal_owner ON projects")
    if _postgres():  # the rows of every organisation, under forced RLS (S39-27)
        op.execute("SELECT set_config('app.current_orgs', '*', true)")
    op.execute("DELETE FROM projects WHERE kind = 'personal'")
    op.drop_index("uq_projects_personal_owner", table_name="projects")
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint(batch.f("ck_projects_personal_has_owner"), type_="check")
        batch.drop_index(batch.f("ix_projects_owner_user_id"))
        batch.drop_constraint(batch.f("fk_projects_owner_user_id_users"), type_="foreignkey")
        batch.drop_column("owner_user_id")
        batch.drop_column("kind")
