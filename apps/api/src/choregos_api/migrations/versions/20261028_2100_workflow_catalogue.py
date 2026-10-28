# SPDX-License-Identifier: Apache-2.0
"""The workflow catalogue (S49-05, ADR 0108): `org_workflows` and `workflow_defs.origin`

`org_workflows` holds the workflows an organisation shared from its projects, one row per share, under
forced RLS like every other table of an organisation. `workflow_defs.origin` records the catalogue entry
an adopted workflow came from. A new table and a nullable column: no row is written, nothing to scope.
The downgrade drops both; adopted workflows keep `source: catalogue` and their text.

Revision ID: e2a4c6e8f0b3
Revises: c9e1a3b5d7f8
Create Date: 2026-10-28 21:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a4c6e8f0b3"
down_revision: str | None = "c9e1a3b5d7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "org_workflows"


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("workflow_defs") as batch:
        batch.add_column(sa.Column("origin", sa.String(length=200), nullable=True))
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("yaml", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=80), nullable=False),
        sa.Column("shared_from", sa.String(length=200), nullable=False),
        sa.Column("shared_by", sa.String(length=200), nullable=True),
        *[
            sa.Column(
                name,
                sa.DateTime(timezone=True),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=False,
            )
            for name in ("created_at", "updated_at")
        ],
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f(f"fk_{TABLE}_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{TABLE}")),
        sa.UniqueConstraint("org_id", "name", "version", name="uq_org_workflows_org_name_version"),
    )
    op.create_index(op.f(f"ix_{TABLE}_org_id"), TABLE, ["org_id"], unique=False)
    if _postgres():
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {TABLE}_org_isolation ON {TABLE}
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    op.drop_index(op.f(f"ix_{TABLE}_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
    with op.batch_alter_table("workflow_defs") as batch:
        batch.drop_column("origin")
