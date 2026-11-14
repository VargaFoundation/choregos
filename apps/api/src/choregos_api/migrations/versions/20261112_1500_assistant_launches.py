# SPDX-License-Identifier: Apache-2.0
"""Assistant launches: `assistant_launch_bindings`, and the kind and target of a conversation action
(S66-22, ADR 0158)

`assistant_launch_bindings` binds a launch of an installation to a project and the name of one of its
workflows, under forced RLS on the organisation like `assistant_tool_bindings`; deleting the project or the
installation deletes its bindings. `conversation_actions` gains `kind` (`write` for every existing row, the
server default) and `target` (the project and workflow a proposed launch names; null for a write). Nothing
of the existing policies moves: the owner-only RESTRICTIVE policy of `conversation_actions` covers the new
columns.

The downgrade drops the table and the two columns: which launches were bound, and which actions were
launches, is lost (an executed launch's work item stays in its project).

Revision ID: a3c5e7f9b1d4
Revises: c7e2a9d4f1b6
Create Date: 2026-11-12 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "a3c5e7f9b1d4"
down_revision: str | None = "c7e2a9d4f1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "assistant_launch_bindings"
INDEXED = ("org_id", "installation_id", "project_id")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("installation_id", sa.String(length=36), nullable=False),
        sa.Column("launch", sa.String(length=63), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("workflow", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("bound_by", sa.String(length=320), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_assistant_launch_bindings_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["assistant_installations.id"],
            name=op.f("fk_assistant_launch_bindings_installation_id_assistant_installations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_assistant_launch_bindings_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_launch_bindings")),
        sa.UniqueConstraint("installation_id", "launch", name="uq_assistant_launch_bindings_launch"),
    )
    for column in INDEXED:
        op.create_index(op.f(f"ix_{TABLE}_{column}"), TABLE, [column], unique=False)
    with op.batch_alter_table("conversation_actions") as batch:
        batch.add_column(sa.Column("kind", sa.String(length=16), server_default="write", nullable=False))
        batch.add_column(sa.Column("target", Json(), nullable=True))
    if _postgres():
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {TABLE}_org_isolation ON {TABLE}
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    with op.batch_alter_table("conversation_actions") as batch:
        batch.drop_column("target")
        batch.drop_column("kind")
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    for column in reversed(INDEXED):
        op.drop_index(op.f(f"ix_{TABLE}_{column}"), table_name=TABLE)
    op.drop_table(TABLE)
