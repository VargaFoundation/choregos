# SPDX-License-Identifier: Apache-2.0
"""Agent ownership scopes and forks (S39-09, ADR 0061): `agents.scope`, `project_id`, `forked_from_*`

Every existing agent becomes an `org` agent, except those installed from the built-in catalogue —
version 1 authored `catalogue:<slug>@<v>` — which become `platform` (common) agents. No agent changes
hands and no pin moves.

Revision ID: 5b7d9f1a3c4e
Revises: e7a9c1b3d5f7
Create Date: 2026-10-15 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b7d9f1a3c4e"
down_revision: str | None = "e7a9c1b3d5f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The test `services/catalogue_d_agents.py` applies to tell a catalogue agent from an organisation's own.
BACKFILL = (
    "UPDATE agents SET scope = 'platform' WHERE id IN "
    "(SELECT agent_id FROM agent_versions WHERE version = 1 AND created_by LIKE 'catalogue:%')"
)


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("scope", sa.String(length=16), server_default="org", nullable=False))
        batch.add_column(sa.Column("project_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("forked_from_agent_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("forked_from_slug", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("forked_from_version", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_agents_project_id_projects"), "projects", ["project_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_foreign_key(
            op.f("fk_agents_forked_from_agent_id_agents"),
            "agents",
            ["forked_from_agent_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index(op.f("ix_agents_scope"), ["scope"])
        batch.create_index(op.f("ix_agents_project_id"), ["project_id"])
    op.execute(BACKFILL)
    with op.batch_alter_table("agents") as batch:
        batch.create_check_constraint(op.f("ck_agents_scope_project"), "(scope = 'project') = (project_id IS NOT NULL)")


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_constraint(op.f("ck_agents_scope_project"), type_="check")
        batch.drop_index(op.f("ix_agents_project_id"))
        batch.drop_index(op.f("ix_agents_scope"))
        batch.drop_constraint(op.f("fk_agents_forked_from_agent_id_agents"), type_="foreignkey")
        batch.drop_constraint(op.f("fk_agents_project_id_projects"), type_="foreignkey")
        batch.drop_column("forked_from_version")
        batch.drop_column("forked_from_slug")
        batch.drop_column("forked_from_agent_id")
        batch.drop_column("project_id")
        batch.drop_column("scope")
