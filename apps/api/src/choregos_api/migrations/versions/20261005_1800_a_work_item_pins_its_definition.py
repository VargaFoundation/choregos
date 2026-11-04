# SPDX-License-Identifier: Apache-2.0
"""A project carries several workflows; each ticket is pinned to its version (ADR 0031)

- `projects.default_workflow`: the name of the default workflow, taken from each project's
  active workflow;
- a single active version PER NAME (partial unique index): before laying it, only the highest
  version of the default stays active, so no project gains a second workflow through the backfill;
- `work_items.workflow_def_id` receives the version that `load_context` would reload at the next
  start: the active version of the default. It never wrote it.

Under PostgreSQL, the tables are under forced RLS and the migration Job runs under the
application's role: without `set_config('app.current_orgs', '*', true)`, each backfill `UPDATE`
would touch zero rows, silently.

Revision ID: a7c9e1f3b5d8
Revises: f6b8d0e2a4c6
Create Date: 2026-10-05 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1f3b5d8"
down_revision: str | None = "f6b8d0e2a4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("default_workflow", sa.String(length=64), nullable=True))

    active = connection.execute(
        sa.text(
            "SELECT id, project_id, name, version FROM workflow_defs WHERE is_active "
            "ORDER BY project_id, version DESC"
        )
    ).all()
    kept: dict[str, tuple[str, str]] = {}
    for ident, project, name, _version in active:
        kept.setdefault(project, (ident, name))  # the highest active version of the project
    for ident, project, _name, _version in active:
        if kept[project][0] != ident:
            connection.execute(
                sa.text("UPDATE workflow_defs SET is_active = :no WHERE id = :id"),
                {"no": False, "id": ident},
            )
    for project, (ident, name) in kept.items():
        connection.execute(
            sa.text("UPDATE projects SET default_workflow = :name WHERE id = :project"),
            {"name": name, "project": project},
        )
        connection.execute(
            sa.text(
                "UPDATE work_items SET workflow_def_id = :wf WHERE project_id = :project AND workflow_def_id IS NULL"
            ),
            {"wf": ident, "project": project},
        )

    op.create_index(
        "uq_workflow_defs_actif_par_nom",
        "workflow_defs",
        ["project_id", "name"],
        unique=True,
        sqlite_where=sa.text("is_active"),
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_work_items_workflow_def_id", "work_items", ["workflow_def_id"])


def downgrade() -> None:
    op.drop_index("ix_work_items_workflow_def_id", table_name="work_items")
    op.drop_index("uq_workflow_defs_actif_par_nom", table_name="workflow_defs")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("default_workflow")
