# SPDX-License-Identifier: Apache-2.0
"""The audit belongs to an organisation

`audit_log` was the last mutation table outside RLS, and it did not even have an organisation
column. `GET /api/v1/audit` applied no filter: having `audit:read` in ONE organisation was
enough to read the audit of ALL of them: the project slugs, the actions, the actor identifiers
of every tenant of the instance. The state of play of 2026-09-24 had missed this point; it came
out of the inventory of 2026-09-26.

`org_id` is nullable on purpose: a sign-in or an API token belongs to no organisation. The
policy therefore lets these rows through **only** for a session of scope `*` (platform,
orchestrator, run token), fail-closed like the twelve others.

Revision ID: c3e5a7f9b1d4
Revises: b2d4f6a8c0e1
Create Date: 2026-09-26 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c3e5a7f9b1d4'
down_revision: str | None = 'b2d4f6a8c0e1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # `batch_alter_table` because SQLite cannot add a constraint afterwards: it
    # recreates the table. The test `test_migrations` compares the models to the migrations on SQLite,
    # and a foreign key laid only under PostgreSQL rightly turned it red.
    with op.batch_alter_table("audit_log") as batch:
        batch.add_column(sa.Column("org_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_audit_log_org_id_organizations",
            "organizations",
            ["org_id"],
            ["id"],
            ondelete="CASCADE",
        )
    op.create_index("ix_audit_log_org_id", "audit_log", ["org_id"])
    if not _postgres():
        return
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS audit_log_org_isolation ON audit_log")
    # `USING` bounds READING, `WITH CHECK` bounds WRITING, and the two differ here.
    # Without an explicit `WITH CHECK`, PostgreSQL reuses `USING` at INSERT, and signing in
    # itself became impossible: `auth.login` writes a trace WITHOUT an organisation (it
    # precedes the resolution), from a session already bounded to the principal's organisations.
    # The first draft of this migration made every API test fail on
    # "new row violates row-level security policy for table audit_log". A bounded session
    # can therefore WRITE a platform trace, which it will not be able to read back, but never
    # a trace attributed to ANOTHER organisation.
    op.execute(
        """
        CREATE POLICY audit_log_org_isolation ON audit_log
        USING (
          '*' = ANY(choregos_current_orgs())
          OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
        )
        WITH CHECK (
          '*' = ANY(choregos_current_orgs())
          OR org_id IS NULL
          OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
        )
        """
    )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS audit_log_org_isolation ON audit_log")
        op.execute("ALTER TABLE audit_log NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_audit_log_org_id", table_name="audit_log")
    with op.batch_alter_table("audit_log") as batch:
        batch.drop_constraint("fk_audit_log_org_id_organizations", type_="foreignkey")
        batch.drop_column("org_id")
