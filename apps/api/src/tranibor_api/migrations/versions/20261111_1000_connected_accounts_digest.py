# SPDX-License-Identifier: Apache-2.0
"""Connected accounts remember what the person consented to: `connected_accounts.provider_digest` (S63-08 review)

The digest of the source's authorization server, client id and address at consent. A connector's editor who
moves one of them no longer receives the person's refresh token or access token: the account asks to be
connected again. Existing accounts have no digest, so each asks once to be connected again (fail closed).
The downgrade drops the column.

Revision ID: a5e5c0f0e0d5
Revises: f4a6c8e0b2d4
Create Date: 2026-11-11 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5e5c0f0e0d5"
down_revision: str | None = "f4a6c8e0b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("connected_accounts", sa.Column("provider_digest", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("connected_accounts", "provider_digest")
