# SPDX-License-Identifier: Apache-2.0
"""Les actions gouvernées dans le cœur (ADR 0035) : `actions` et le journal de leurs effets

Sous la RLS forcée, comme le reste. Un effet est consigné sous sa clé (`<action>:<n>`, unique)
avant d'être tenté : c'est ce qui empêche une reprise de le refaire.

Revision ID: f8a0b2c4d6e9
Revises: e7f9a1b3c5d8
Create Date: 2026-10-05 23:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "f8a0b2c4d6e9"
down_revision: str | None = "e7f9a1b3c5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("actions", "action_effects")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("params", Json(), nullable=False),
        sa.Column("effects", Json(), nullable=False),
        sa.Column("proposed_by", Json(), nullable=False),
        sa.Column("approval", Json(), nullable=False),
        sa.Column("decisions", Json(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("result", Json(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("temporal_wf_id", sa.String(length=128), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_actions_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"], name=op.f("fk_actions_project_id_projects"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name=op.f("fk_actions_work_item_id_work_items"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actions")),
    )
    op.create_index(op.f("ix_actions_org_id"), "actions", ["org_id"], unique=False)
    op.create_index(op.f("ix_actions_project_id"), "actions", ["project_id"], unique=False)
    op.create_index(op.f("ix_actions_work_item_id"), "actions", ["work_item_id"], unique=False)
    op.create_index("ix_actions_project_status", "actions", ["project_id", "status"], unique=False)
    op.create_table(
        "action_effects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("effect", sa.String(length=128), nullable=False),
        sa.Column("params", Json(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", Json(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_action_effects_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["action_id"], ["actions.id"], name=op.f("fk_action_effects_action_id_actions"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_action_effects")),
        sa.UniqueConstraint("action_id", "position", name="uq_action_effects_action_position"),
        sa.UniqueConstraint("key", name=op.f("uq_action_effects_key")),
    )
    op.create_index(op.f("ix_action_effects_org_id"), "action_effects", ["org_id"], unique=False)
    op.create_index(op.f("ix_action_effects_action_id"), "action_effects", ["action_id"], unique=False)

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
    op.drop_index(op.f("ix_action_effects_action_id"), table_name="action_effects")
    op.drop_index(op.f("ix_action_effects_org_id"), table_name="action_effects")
    op.drop_table("action_effects")
    op.drop_index("ix_actions_project_status", table_name="actions")
    op.drop_index(op.f("ix_actions_work_item_id"), table_name="actions")
    op.drop_index(op.f("ix_actions_project_id"), table_name="actions")
    op.drop_index(op.f("ix_actions_org_id"), table_name="actions")
    op.drop_table("actions")
