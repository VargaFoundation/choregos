# SPDX-License-Identifier: Apache-2.0
"""A decision record without a run, the routing's (S52-11, ADR 0123)

`decision_records.run_id` becomes nullable: a work item's routing by a decision model is recorded at
its birth, before any run (`source = router`). A record without a run is unique on
`(work_item_id, question)`, by a partial index: the unique `(run_id, question)` never compares two
nulls. The downgrade deletes the records without a run, drops the index and makes `run_id` required
again.

Revision ID: e7a9c1d3f5b8
Revises: b3d5f7a9c1e2
Create Date: 2026-11-05 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a9c1d3f5b8"
down_revision: str | None = "b3d5f7a9c1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "uq_decision_records_work_item_question_without_run"


def upgrade() -> None:
    with op.batch_alter_table("decision_records") as batch:
        batch.alter_column("run_id", existing_type=sa.String(length=128), nullable=True)
    op.create_index(
        INDEX,
        "decision_records",
        ["work_item_id", "question"],
        unique=True,
        postgresql_where=sa.text("run_id IS NULL"),
        sqlite_where=sa.text("run_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(INDEX, table_name="decision_records")
    if op.get_bind().dialect.name == "postgresql":
        # Under forced RLS the migration job's role sees no row until every organisation is opened.
        op.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    op.execute(sa.text("DELETE FROM decision_records WHERE run_id IS NULL"))
    with op.batch_alter_table("decision_records") as batch:
        batch.alter_column("run_id", existing_type=sa.String(length=128), nullable=False)
