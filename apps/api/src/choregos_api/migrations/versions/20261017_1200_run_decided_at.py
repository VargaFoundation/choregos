# SPDX-License-Identifier: Apache-2.0
"""A run remembers that its outcome was decided (S22-49, #552): `runs.decided_at`

A restarted interpreter restores each transition's attempts from its runs (#550). A succeeded last
run is read back, in case the interpreter died before applying its decision; but a transition the
workflow plays again by itself (a review asking for changes sends the work item back) then read back
the EARLIER pass's run instead of playing a new attempt. `decided_at` tells the two apart: the
interpreter writes it with the state its decision moved the work item to.

Backfill: every run that is over is decided (at its end), except the one a restart reads back today
— the last attempt of its transition, succeeded, on a work item still in that transition's
source state. For that one the table cannot tell "decision not recorded" from "earlier pass", and
keeping it undecided keeps the behaviour of #550: no stage paid twice, no commit pushed twice. A run
still queued or running stays undecided: a restart awaits it.

Revision ID: c5e7a9b1d3f6
Revises: a7c9e1b3d5f8
Create Date: 2026-10-17 12:00:00+00:00
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "c5e7a9b1d3f6"
down_revision: str | None = "a7c9e1b3d5f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: A run in one of these states is over (`activities/interpretation.py`, `FINISHED_RUNS`).
FINISHED = ("succeeded", "failed", "cancelled", "timed_out")


def _source_state(stage_input: Any) -> str | None:
    """The state the run's transition starts from, as its stage input recorded it."""
    if isinstance(stage_input, str):
        try:
            stage_input = json.loads(stage_input)
        except ValueError:
            return None
    if not isinstance(stage_input, dict):
        return None
    transition = stage_input.get("transition")
    source = transition.get("from") if isinstance(transition, dict) else None
    return str(source) if source else None


def decided(rows: Sequence[Any]) -> list[dict[str, Any]]:
    """The finished runs to mark decided, with the time: all but the one a restart reads back.

    Each row: id, work_item_id, transition_id, attempt, status, stage_input, ended_at, updated_at,
    and the work item's current state.
    """
    last: dict[tuple[str, str], Any] = {}
    for row in rows:
        if row.transition_id is None:
            continue
        key = (str(row.work_item_id), str(row.transition_id))
        if key not in last or int(row.attempt or 0) > int(last[key].attempt or 0):
            last[key] = row
    pending = {
        str(row.id)
        for row in last.values()
        if row.status == "succeeded" and _source_state(row.stage_input) in (None, row.item_state)
    }
    return [
        {"id": str(row.id), "decided_at": row.ended_at or row.updated_at}
        for row in rows
        if str(row.id) not in pending
    ]


def upgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Forced RLS, and the migration job runs as the application role: see every organisation.
        bind.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    rows = bind.execute(
        sa.text(
            "SELECT r.id, r.work_item_id, r.transition_id, r.attempt, r.status, r.stage_input,"
            " r.ended_at, r.updated_at, w.state AS item_state"
            " FROM runs r JOIN work_items w ON w.id = r.work_item_id"
            " WHERE r.decided_at IS NULL AND r.status IN :finished"
        ).bindparams(sa.bindparam("finished", expanding=True)),
        {"finished": list(FINISHED)},
    ).all()
    marks = decided(rows)
    if marks:
        bind.execute(sa.text("UPDATE runs SET decided_at = :decided_at WHERE id = :id"), marks)


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("decided_at")
