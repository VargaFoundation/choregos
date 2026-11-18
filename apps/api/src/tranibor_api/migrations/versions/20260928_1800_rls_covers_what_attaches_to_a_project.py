# SPDX-License-Identifier: Apache-2.0
"""RLS covers what attaches to a project: run events, deployments, gateway keys

Thirteen tables were under RLS; three others carry an organisation's data without
being so. A member of `a` could not read them through the API (the routes filter), but a
session bounded to `a` saw them all in the database, and the database must hold when a route
forgets a filter:

- `run_events`: an agent's journal: commands, outputs, a client's code excerpts;
- `deployments`: when, where and what an organisation puts into production;
- `gateway_keys`: the spend ceilings of each run.

Each attaches to a project (through its run, its release, or directly), hence to an
organisation: the policy reuses `choregos_project_org`, like the original tables. A gateway
key WITHOUT a project is visible only to the platform (`*`): no path writes one
today, and the day there is one, fail-closed is the right default.

What stays outside RLS, and why, is written in `tests/test_rls_postgres.py`
(`EXEMPTED`): a test there refuses any new table that is neither under RLS nor exempted with its
reason.

Revision ID: d4f6b8a0c2e3
Revises: c3e5a7f9b1d4
Create Date: 2026-09-28 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d4f6b8a0c2e3"
down_revision: str | None = "c3e5a7f9b1d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: table -> expression that returns the slug of the organisation owning the row.
ATTACHED = {
    "run_events": "choregos_run_org(run_id)",
    "deployments": "choregos_release_org(release_id)",
    "gateway_keys": "choregos_project_org(project_id)",
}


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _postgres():
        return
    # Rights of the CALLER (not SECURITY DEFINER): the run or the release must themselves be
    # visible to the session: the RLS of `runs` and `releases` applies to the sub-select.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION choregos_run_org(rid text) RETURNS text AS $$
          SELECT choregos_project_org(r.project_id) FROM runs r WHERE r.id = rid
        $$ LANGUAGE sql STABLE;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION choregos_release_org(rid text) RETURNS text AS $$
          SELECT choregos_project_org(r.project_id) FROM releases r WHERE r.id = rid
        $$ LANGUAGE sql STABLE;
        """
    )
    for table, organisation in ATTACHED.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_org_isolation ON {table}
            USING (
              '*' = ANY(choregos_current_orgs())
              OR {organisation} = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if not _postgres():
        return
    for table in ATTACHED:
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS choregos_release_org(text)")
    op.execute("DROP FUNCTION IF EXISTS choregos_run_org(text)")
