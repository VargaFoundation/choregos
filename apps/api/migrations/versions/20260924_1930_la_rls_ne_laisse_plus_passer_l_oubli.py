"""La RLS ne laisse plus passer l'oubli : fail-closed, et une portée par session

La politique initiale était `choregos_current_org() IS NULL OR …` : une session qui ne
posait pas `app.current_org` voyait tout. Aucun chemin de production ne le posait — ni
`get_db()`, ni l'orchestrateur, ni les seeds — donc la RLS n'a jamais rien isolé (état des
lieux du 2026-09-24). Désormais :

- `app.current_orgs` porte une liste d'organisations (`a,b`) ou `*` ;
- absent ou vide ⇒ AUCUNE ligne des tables sous RLS ;
- `projects` est jugé sur `org_id` directement, parce qu'à l'INSERT la ligne n'est pas
  encore visible du sous-select qui joignait `projects` : la création d'un projet aurait
  été refusée à l'organisation même qui la demandait.

Revision ID: b2d4f6a8c0e1
Revises: a1c3e5f7b9d2
Create Date: 2026-09-24 19:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = 'b2d4f6a8c0e1'
down_revision: str | None = 'a1c3e5f7b9d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RLS_TABLES = (
    "projects",
    "connectors",
    "workflow_defs",
    "policies",
    "work_items",
    "runs",
    "human_requests",
    "events",
    "findings",
    "releases",
    "cost_ledger",
    "memory_facts",
)


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _postgres():
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION choregos_current_orgs() RETURNS text[] AS $$
          SELECT CASE
            WHEN coalesce(current_setting('app.current_orgs', true), '') = '' THEN ARRAY[]::text[]
            ELSE string_to_array(current_setting('app.current_orgs', true), ',')
          END
        $$ LANGUAGE sql STABLE;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION choregos_org_slug(oid_ text) RETURNS text AS $$
          SELECT o.slug FROM organizations o WHERE o.id = oid_
        $$ LANGUAGE sql STABLE;
        """
    )
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        if table == "projects":
            visible = "choregos_org_slug(org_id)"
        else:
            visible = "choregos_project_org(project_id)"
        op.execute(
            f"""
            CREATE POLICY {table}_org_isolation ON {table}
            USING (
              '*' = ANY(choregos_current_orgs())
              OR {visible} = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if not _postgres():
        return
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        column = "id" if table == "projects" else "project_id"
        op.execute(
            f"""
            CREATE POLICY {table}_org_isolation ON {table}
            USING (
              choregos_current_org() IS NULL
              OR choregos_project_org({column}) = choregos_current_org()
            )
            """
        )
    op.execute("DROP FUNCTION IF EXISTS choregos_org_slug(text)")
    op.execute("DROP FUNCTION IF EXISTS choregos_current_orgs()")
