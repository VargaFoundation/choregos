# SPDX-License-Identifier: Apache-2.0
"""The issuer that gave an account its subject (S70-20, ADR 0160)

`users.oidc_sub` is unique, and a subject only means something at the issuer that gave it (OpenID
Connect Core 1.0 §2). Changing the instance's identity provider changes every subject: a re-link window
moves an account from one provider's subject to the new one's. To know which accounts the new provider
already holds, and never to move an account twice, each sign-in through an OIDC provider records the
issuer beside the subject.

A nullable column, no row rewritten: an account bound before this revision keeps a null issuer until its
next sign-in records it. The policies of row-level security on `users` are the table's and do not move.
The downgrade drops the column.

Revision ID: f0b2d4e6a8c1
Revises: b3dbe73fff38
Create Date: 2026-11-12 16:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0b2d4e6a8c1"
down_revision: str | None = "b3dbe73fff38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("oidc_issuer", sa.String(length=512), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "oidc_issuer")
