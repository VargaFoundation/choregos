# SPDX-License-Identifier: Apache-2.0
"""The SIEM export's cursor: how far each sink has been given each audit chain (S80-05b, ADR 0196)

`audit_export_cursors` holds, per sink and per chain (an organisation's id, or `''` for the platform's),
the `seq` and `hash` of the last entry the sink accepted. The export reads the entries after it, ships them,
and moves it in the transaction that holds the row's lock. Nothing else reads or writes it.

On PostgreSQL the table is under forced row-level security, read and written by the platform's scope only
(the export runs as the platform: it reads every chain). Not append-only and not evidence: the chain is
evidence, and a lost cursor re-ships a chain from its first entry.

The downgrade drops the table: the next export of a sink starts from entry 1.

Revision ID: a9c1e3b5d7f0
Revises: b4d6f8a0c2e5
Create Date: 2026-11-20 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9c1e3b5d7f0"
down_revision: str | None = "b4d6f8a0c2e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "audit_export_cursors"
WHOLE = "'*' = ANY(choregos_current_orgs())"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("sink", sa.String(64), nullable=False),
        sa.Column("chain", sa.String(36), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_audit_export_cursors_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sink", "chain", name=op.f("pk_audit_export_cursors")),
    )
    op.create_index(op.f("ix_audit_export_cursors_org_id"), TABLE, ["org_id"])
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_org_isolation ON {TABLE}")
    op.execute(f"CREATE POLICY {TABLE}_org_isolation ON {TABLE} USING ({WHOLE}) WITH CHECK ({WHOLE})")


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_export_cursors_org_id"), table_name=TABLE)
    op.drop_table(TABLE)
