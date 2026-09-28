# SPDX-License-Identifier: Apache-2.0
"""La RLS couvre ce qui se rattache à un projet : événements de run, déploiements, clés de passerelle

Treize tables étaient sous RLS ; trois autres portent des données d'une organisation sans
l'être. Un membre de `a` ne pouvait pas les lire par l'API — les routes filtrent — mais une
session bornée à `a` les voyait toutes en base, et c'est la base qui doit tenir quand une route
oublie un filtre :

- `run_events` : le journal d'un agent — commandes, sorties, extraits de code d'un client ;
- `deployments` : quand, où et quoi une organisation met en production ;
- `gateway_keys` : les plafonds de dépense de chaque run.

Chacune se rattache à un projet (par son run, sa release, ou directement), donc à une
organisation : la politique réutilise `choregos_project_org`, comme les tables d'origine. Une clé
de passerelle SANS projet n'est visible que de la plateforme (`*`) : aucun chemin n'en écrit
aujourd'hui, et le jour où il y en aura, fail-closed est le bon défaut.

Ce qui reste hors RLS, et pourquoi, est écrit dans `tests/test_rls_postgres.py`
(`EXEMPTEES`) : un test y refuse toute table nouvelle qui n'est ni sous RLS ni exemptée avec sa
raison.

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

#: table → expression qui rend le slug de l'organisation propriétaire de la ligne.
RATTACHEES = {
    "run_events": "choregos_run_org(run_id)",
    "deployments": "choregos_release_org(release_id)",
    "gateway_keys": "choregos_project_org(project_id)",
}


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _postgres():
        return
    # Droits de l'APPELANT (pas SECURITY DEFINER) : le run ou la release doivent eux-mêmes être
    # visibles de la session — la RLS de `runs` et `releases` s'applique au sous-select.
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
    for table, organisation in RATTACHEES.items():
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
    for table in RATTACHEES:
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS choregos_release_org(text)")
    op.execute("DROP FUNCTION IF EXISTS choregos_run_org(text)")
