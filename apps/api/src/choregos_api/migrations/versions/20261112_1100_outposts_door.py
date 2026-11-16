# SPDX-License-Identifier: Apache-2.0
"""The outposts' door: the poll slot, a call's time to live, its asserted names, its answer (S65-12, ADR 0148)

What the door and the rendezvous keep that the first migration left no place for:

- on `outpost_instances`, the one poll an instance has waiting (`poll_holder`, a token, and
  `poll_expires_at`, when the slot lapses by itself): the replicas share a row and nothing else, so the
  slot is on it (ADR 0148 §7); and `poll_conflict_at`, when a second poll under the same key last
  raised an alert;
- on `outpost_calls`, `expires_at` (when the platform stops waiting for the call), `asserted` (who
  the platform says the call is made for, ADR 0149 §3), `answer_key` (the public half of the X25519
  key the answer is sealed to), the five `answer_key_*` columns (its private half, in the envelope of
  `org_secrets`, like the arguments beside it) and `answer_sealed` (the answer as it crossed the door:
  the door stores it and does not open it).

Row-level security is a row's, not a column's: these columns are read and written under the policies
`20261112_1000_outposts` put on their tables (the organisation's, and the outpost the session names),
with nothing to add. Every column is nullable and no row is rewritten. The downgrade drops them, and
with them whatever answer was still waiting to be read.

Revision ID: d7f9b1c3e5a8
Revises: c6e8a0b2d4f7
Create Date: 2026-11-12 11:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "d7f9b1c3e5a8"
down_revision: str | None = "c6e8a0b2d4f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> dict[str, list[sa.Column[Any]]]:
    return {
        "outpost_instances": [
            sa.Column("poll_holder", sa.String(length=32), nullable=True),
            sa.Column("poll_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("poll_conflict_at", sa.DateTime(timezone=True), nullable=True),
        ],
        "outpost_calls": [
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("asserted", Json(), nullable=True),
            sa.Column("answer_key", sa.String(length=43), nullable=True),
            sa.Column("answer_key_ciphertext", sa.LargeBinary(), nullable=True),
            sa.Column("answer_key_nonce", sa.LargeBinary(), nullable=True),
            sa.Column("answer_key_wrapped_dek", sa.LargeBinary(), nullable=True),
            sa.Column("answer_key_dek_nonce", sa.LargeBinary(), nullable=True),
            sa.Column("answer_key_kek_id", sa.String(length=32), nullable=True),
            sa.Column("answer_sealed", sa.Text(), nullable=True),
        ],
    }


def upgrade() -> None:
    for table, columns in _columns().items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.add_column(column)


def downgrade() -> None:
    for table, columns in reversed(_columns().items()):
        with op.batch_alter_table(table) as batch:
            for column in reversed(columns):
                batch.drop_column(column.name)
