# SPDX-License-Identifier: Apache-2.0
"""The ontology's tables are dropped with its package (ADR 0181, O4-1)

The ontology was a trial plugin with its own Alembic branch (`onto0001` to `onto0003`), applied only
where `CHOREGOS_ONTOLOGY_TRIAL` was set. The package is gone; an installation that enabled the trial
still holds `ontology_versions` and `managed_objects` (and `action_proposals`, if it stopped at
`onto0002`). They are dropped when they exist, `action_proposals` first since it refers to
`ontology_versions`; their RLS policies and indexes go with them. An installation that never enabled the
trial has none of them, and nothing happens.

The branch's row in `alembic_version` is forgotten before this runs (`migrate.forget_retired_revisions`,
called by `env.py`): Alembic could not read the table otherwise.

What the trial persisted in the core's tables stays: actions of origin `ontology`, their effect kinds
`ontology.effet` and `ontology.preuve` and their `params.ontologie` are read as before (ADR 0098 §4). An
ontology action still pending can be rejected; approved, it would fail on its effect, which nothing
declares any more.

The downgrade does not create the tables again: the package that owned them no longer exists.

Revision ID: b4d6f8a0c2e5
Revises: f3c5a7e9b1d2
Create Date: 2026-11-19 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b4d6f8a0c2e5"
down_revision: str | None = "f3c5a7e9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: In the order they can be dropped: `action_proposals.version_id` refers to `ontology_versions`.
TABLES = ("action_proposals", "managed_objects", "ontology_versions")


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    """Nothing: the ontology's package, which owned these tables, is gone."""
