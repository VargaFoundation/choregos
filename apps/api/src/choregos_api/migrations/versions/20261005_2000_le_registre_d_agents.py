# SPDX-License-Identifier: Apache-2.0
"""Le registre d'agents : des agents de l'organisation, leurs versions immuables, l'épingle des projets (ADR 0033)

Les trois tables portent l'organisation et passent sous la RLS forcée, comme les autres : un agent
de `b` n'existe pas pour une session bornée à `a`, même si une route oubliait de filtrer.

Revision ID: e1f3a5b7c9d2
Revises: c9e1a3b5d7f0
Create Date: 2026-10-05 20:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "e1f3a5b7c9d2"
down_revision: str | None = "c9e1a3b5d7f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = ("agents", "agent_versions", "project_agents")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            ["org_id"], ["organizations.id"], name=op.f("fk_agents_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name=op.f("fk_agents_owner_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agents")),
        sa.UniqueConstraint("org_id", "slug", name="uq_agents_org_slug"),
    )
    op.create_index(op.f("ix_agents_org_id"), "agents", ["org_id"], unique=False)
    op.create_table(
        "agent_versions",
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", Json(), nullable=False),
        sa.Column("checksum", sa.String(length=80), nullable=False),
        sa.Column("created_by", sa.String(length=200), nullable=True),
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
            ["agent_id"], ["agents.id"], name=op.f("fk_agent_versions_agent_id_agents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_agent_versions_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_versions")),
        sa.UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
    )
    op.create_index(op.f("ix_agent_versions_agent_id"), "agent_versions", ["agent_id"], unique=False)
    op.create_index(op.f("ix_agent_versions_org_id"), "agent_versions", ["org_id"], unique=False)
    op.create_table(
        "project_agents",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("overrides", Json(), nullable=False),
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
            ["agent_id"], ["agents.id"], name=op.f("fk_project_agents_agent_id_agents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_project_agents_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_agents_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_agents")),
        sa.UniqueConstraint("project_id", "agent_id", name="uq_project_agents_project_agent"),
    )
    op.create_index(op.f("ix_project_agents_agent_id"), "project_agents", ["agent_id"], unique=False)
    op.create_index(op.f("ix_project_agents_org_id"), "project_agents", ["org_id"], unique=False)
    op.create_index(op.f("ix_project_agents_project_id"), "project_agents", ["project_id"], unique=False)

    if _postgres():
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"""
                CREATE POLICY {table}_org_isolation ON {table}
                USING (
                  '*' = ANY(choregos_current_orgs())
                  OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
                )
                """
            )


def downgrade() -> None:
    if _postgres():
        for table in TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    op.drop_index(op.f("ix_project_agents_project_id"), table_name="project_agents")
    op.drop_index(op.f("ix_project_agents_org_id"), table_name="project_agents")
    op.drop_index(op.f("ix_project_agents_agent_id"), table_name="project_agents")
    op.drop_table("project_agents")
    op.drop_index(op.f("ix_agent_versions_org_id"), table_name="agent_versions")
    op.drop_index(op.f("ix_agent_versions_agent_id"), table_name="agent_versions")
    op.drop_table("agent_versions")
    op.drop_index(op.f("ix_agents_org_id"), table_name="agents")
    op.drop_table("agents")
