# SPDX-License-Identifier: Apache-2.0
"""One head again: S66-12's assistant knowledge bindings and S65-26's settlement both followed the first join

Both were merged on 2026-11-14 after `cc44662df8d6`, each tested on a main without the other, and the core had two
heads again. As with `20261114_1350_one_head_again.py`, neither revision is rewritten, since a database may already
have applied either: this one joins them and does nothing else.

Revision ID: dcdaeba28e08
Revises: f9b1d3e5a7c2, 4e0fe4b219c6
Create Date: 2026-11-14 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "dcdaeba28e08"
down_revision: str | Sequence[str] | None = ("f9b1d3e5a7c2", "4e0fe4b219c6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Nothing: the two branches it joins carry their own changes."""


def downgrade() -> None:
    """Nothing: going down splits the two branches again."""
