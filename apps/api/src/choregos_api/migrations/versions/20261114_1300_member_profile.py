# SPDX-License-Identifier: Apache-2.0
"""A member's profile: what the identity provider says of them, and their photo (S66-42, ADR 0162)

- `users.given_name`, `family_name`, `job_title`, `department`: nullable, filled at each identity-provider
  sign-in from the claims that carry them; NULL for every existing person until their next sign-in.
- `user_avatars`: one photo per person, the WebP bytes the API re-encoded from their upload. Under forced
  RLS with the policy of `api_tokens` (ADR 0026): read by the person and by whoever sees them, written by the
  person only (or the platform scope).

Revision ID: 5455f59749bd
Revises: c7e2a9d4f1b6
Create Date: 2026-11-14 13:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5455f59749bd"
down_revision: str | None = "c7e2a9d4f1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "user_avatars"
CLAIMS = ("given_name", "family_name", "job_title", "department")
WHOLE = "'*' = ANY(choregos_current_orgs())"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        for claim in CLAIMS:
            batch.add_column(sa.Column(claim, sa.String(length=200), nullable=True))
    op.create_table(
        TABLE,
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f(f"fk_{TABLE}_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f(f"pk_{TABLE}")),
    )
    if _postgres():
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {TABLE}_org_isolation ON {TABLE} "
            f"USING ({WHOLE} OR user_id = choregos_utilisateur_courant() OR choregos_utilisateur_visible(user_id)) "
            f"WITH CHECK ({WHOLE} OR user_id = choregos_utilisateur_courant())"
        )


def downgrade() -> None:
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    op.drop_table(TABLE)
    with op.batch_alter_table("users") as batch:
        for claim in reversed(CLAIMS):
            batch.drop_column(claim)
