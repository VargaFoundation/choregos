# SPDX-License-Identifier: Apache-2.0
"""Who spent it (S29-01): `cost_ledger.agent_slug|agent_version|workflow_name|workflow_version`

Backfilled from the run each line points at (its agent) and the work item's workflow definition.
A line without a run, or whose run was deleted, keeps NULL.

Revision ID: f8b0d2e4a6c8
Revises: 4e6a8c0b2d3f
Create Date: 2026-10-14 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f8b0d2e4a6c8"
down_revision: str | None = "4e6a8c0b2d3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_AGENT = (
    "UPDATE cost_ledger SET "
    "agent_slug = (SELECT r.agent_slug FROM runs r WHERE r.id = cost_ledger.run_id), "
    "agent_version = (SELECT r.agent_version FROM runs r WHERE r.id = cost_ledger.run_id) "
    "WHERE run_id IS NOT NULL"
)
_DEF = "FROM work_items w JOIN workflow_defs d ON d.id = w.workflow_def_id WHERE w.id = cost_ledger.work_item_id"
BACKFILL_WORKFLOW = (
    f"UPDATE cost_ledger SET workflow_name = (SELECT d.name {_DEF}), "
    f"workflow_version = (SELECT d.version {_DEF}) WHERE work_item_id IS NOT NULL"
)


def upgrade() -> None:
    connexion = op.get_bind()
    if connexion.dialect.name == "postgresql":
        connexion.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    with op.batch_alter_table("cost_ledger") as batch:
        batch.add_column(sa.Column("agent_slug", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("agent_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("workflow_name", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("workflow_version", sa.Integer(), nullable=True))
        batch.create_index(op.f("ix_cost_ledger_agent_slug"), ["agent_slug"], unique=False)
        batch.create_index(op.f("ix_cost_ledger_workflow_name"), ["workflow_name"], unique=False)
    connexion.execute(sa.text(BACKFILL_AGENT))
    connexion.execute(sa.text(BACKFILL_WORKFLOW))


def downgrade() -> None:
    with op.batch_alter_table("cost_ledger") as batch:
        batch.drop_index(op.f("ix_cost_ledger_workflow_name"))
        batch.drop_index(op.f("ix_cost_ledger_agent_slug"))
        for colonne in ("workflow_version", "workflow_name", "agent_version", "agent_slug"):
            batch.drop_column(colonne)
