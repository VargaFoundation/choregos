# SPDX-License-Identifier: Apache-2.0
"""The decision ledger: `decision_records` (S52-05, ADR 0121 §6)

One answered question per row — a decision step's answer or the confidence an agent declared — with
its probability, the transition that followed and the outcome observed afterwards; unique on
`(run_id, question)`, so a replayed write adds nothing. Under forced RLS like `received_events`. The
downgrade drops the table, its indexes and its policy.

Revision ID: d5b7c9e1f3a4
Revises: c6e8a0b2d4f1
Create Date: 2026-11-03 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "d5b7c9e1f3a4"
down_revision: str | None = "c6e8a0b2d4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "decision_records",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("work_item_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("work_item_key", sa.String(length=255), nullable=False),
        sa.Column("project_slug", sa.String(length=64), nullable=False),
        sa.Column("workflow", sa.String(length=100), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=True),
        sa.Column("step", sa.String(length=64), nullable=False),
        sa.Column("transition_id", sa.String(length=64), nullable=True),
        sa.Column("question", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("model_version", sa.String(length=200), nullable=True),
        sa.Column("answer", Json(), nullable=False),
        sa.Column("probability", sa.Float(), nullable=False),
        sa.Column("transition", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("overridden", sa.Boolean(), nullable=True),
        sa.Column("terminal", sa.String(length=16), nullable=True),
        sa.Column("reopened", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_decision_records_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_decision_records_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["work_items.id"],
            name=op.f("fk_decision_records_work_item_id_work_items"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.id"],
            name=op.f("fk_decision_records_run_id_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision_records")),
        sa.UniqueConstraint("run_id", "question", name="uq_decision_records_run_question"),
    )
    op.create_index(op.f("ix_decision_records_org_id"), "decision_records", ["org_id"], unique=False)
    op.create_index(op.f("ix_decision_records_project_id"), "decision_records", ["project_id"], unique=False)
    op.create_index(
        op.f("ix_decision_records_work_item_id"), "decision_records", ["work_item_id"], unique=False
    )
    op.create_index(op.f("ix_decision_records_run_id"), "decision_records", ["run_id"], unique=False)
    op.create_index(
        "ix_decision_records_step", "decision_records", ["project_id", "workflow", "step"], unique=False
    )

    if _postgres():
        op.execute("ALTER TABLE decision_records ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE decision_records FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY decision_records_org_isolation ON decision_records
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS decision_records_org_isolation ON decision_records")
    op.drop_index("ix_decision_records_step", table_name="decision_records")
    op.drop_index(op.f("ix_decision_records_run_id"), table_name="decision_records")
    op.drop_index(op.f("ix_decision_records_work_item_id"), table_name="decision_records")
    op.drop_index(op.f("ix_decision_records_project_id"), table_name="decision_records")
    op.drop_index(op.f("ix_decision_records_org_id"), table_name="decision_records")
    op.drop_table("decision_records")
