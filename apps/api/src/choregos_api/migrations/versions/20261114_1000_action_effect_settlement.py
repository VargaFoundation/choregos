# SPDX-License-Identifier: Apache-2.0
"""What a person recorded of an effect in doubt (S65-26, ADR 0149 §5)

An effect sent through an outpost and never answered ends `in_doubt`, and nothing the platform does
by itself moves it on. A person checks the system the write was for and settles it: the write was
made, or it was not. `action_effects.settlement` keeps what they recorded (the outcome, who, when,
their note, the result they stated, the age of their authentication) and the doubt's evidence that
the row held until then (what was sent and never answered). Null on every existing row: no effect
was ever settled. The table already has its row-level security; a column changes nothing of it.

`action_effects.status` is a plain string: the new `not_made` needs no change of it. The downgrade
drops the column; an effect settled `made` stays `done`, one settled `not_made` stays `not_made`,
which the earlier revision reads as an effect neither done nor in doubt.

Revision ID: 4e0fe4b219c6
Revises: a2c4e6f8b0d1
Create Date: 2026-11-14 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "4e0fe4b219c6"
down_revision: str | None = "a2c4e6f8b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("action_effects") as table:
        table.add_column(sa.Column("settlement", Json(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("action_effects") as table:
        table.drop_column("settlement")
