# SPDX-License-Identifier: Apache-2.0
"""Les propositions d'action : décisions, effets et preuves, sous RLS

Revision ID: onto0002
Revises: onto0001
Create Date: 2026-10-03 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "onto0002"
down_revision: str | None = "onto0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "action_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "version_id",
            sa.String(36),
            sa.ForeignKey("ontology_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(63), nullable=False),
        sa.Column("target_ids", _json(), nullable=False),
        sa.Column("params", _json(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("proposed_by", _json(), nullable=False),
        sa.Column("approval", _json(), nullable=False),
        sa.Column("decisions", _json(), nullable=False),
        sa.Column("effects", _json(), nullable=False),
        sa.Column("evidence", _json(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("evidence_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_action_proposals_project_id", "action_proposals", ["project_id"])
    op.create_index("ix_action_proposals_project_status", "action_proposals", ["project_id", "status"])
    colonnes = ["project_id", "action_type", "idempotency_key"]
    op.create_index("ix_action_proposals_idempotency", "action_proposals", colonnes)
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE action_proposals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE action_proposals FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY action_proposals_org_isolation ON action_proposals
        USING (
          '*' = ANY(choregos_current_orgs())
          OR choregos_project_org(project_id) = ANY(choregos_current_orgs())
        )
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS action_proposals_org_isolation ON action_proposals")
    op.drop_table("action_proposals")
