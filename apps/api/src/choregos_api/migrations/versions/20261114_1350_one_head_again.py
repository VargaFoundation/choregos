# SPDX-License-Identifier: Apache-2.0
"""One head again: S66-22's assistant launches and S66-42's member profile both followed the linked accounts

Both were merged on 2026-11-14 after `c7e2a9d4f1b6`, each unaware of the other, and the core had two heads:
`alembic upgrade head` (what `test_migrations.py` runs) refuses that. Neither revision is rewritten, since a
database may already have applied either: this one joins them and does nothing else.

Revision ID: cc44662df8d6
Revises: a3c5e7f9b1d4, 5455f59749bd
Create Date: 2026-11-14 13:50:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "cc44662df8d6"
down_revision: str | Sequence[str] | None = ("a3c5e7f9b1d4", "5455f59749bd")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing: the two branches it joins carry their own changes."""


def downgrade() -> None:
    """Nothing: going down splits the two branches again."""
