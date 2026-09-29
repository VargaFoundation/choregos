# SPDX-License-Identifier: Apache-2.0
"""L'identité sous RLS : organisations, appartenances, utilisateurs, jetons d'API

L'ADR 0026 laissait ces quatre tables hors RLS : le principal était résolu AVANT que la portée soit
posée, et une politique l'aurait aveuglé. Depuis la 0.10.1, l'identité se résout en portée de
plateforme (`deps._principal_from_user`), et la suite entière de l'API tourne sous RLS en CI. La
condition est levée ; l'ADR 0026 est mis à jour.

- `organizations` : visible si son slug est dans la portée ;
- `memberships` : visible si son organisation l'est ;
- `users` : visible si l'utilisateur appartient à une organisation de la portée, ou si c'est
  l'appelant lui-même (`app.current_user`) — un utilisateur sans appartenance doit pouvoir se lire.
  Créer un utilisateur reste permis (`WITH CHECK true`) : l'e-mail est unique à l'échelle de
  l'instance, et une invitation crée l'utilisateur AVANT son appartenance ;
- `api_tokens` : ceux d'un utilisateur visible, ou de l'appelant.

Revision ID: e5a7c9b1d3f5
Revises: d4f6b8a0c2e3
Create Date: 2026-09-29 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5a7c9b1d3f5"
down_revision: str | None = "d4f6b8a0c2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TOUT = "'*' = ANY(choregos_current_orgs())"
POLITIQUES = {
    "organizations": (f"{TOUT} OR slug = ANY(choregos_current_orgs())", None),
    "memberships": (f"{TOUT} OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())", None),
    "users": (f"{TOUT} OR id = choregos_utilisateur_courant() OR choregos_utilisateur_visible(id)", "true"),
    "api_tokens": (
        f"{TOUT} OR user_id = choregos_utilisateur_courant() OR choregos_utilisateur_visible(user_id)",
        f"{TOUT} OR user_id = choregos_utilisateur_courant()",
    ),
}


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _postgres():
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION choregos_utilisateur_courant() RETURNS text AS $$
          SELECT nullif(current_setting('app.current_user', true), '')
        $$ LANGUAGE sql STABLE;
        """
    )
    # Droits de l'APPELANT : `memberships` est elle-même sous RLS, le sous-select ne voit que les
    # appartenances de la portée.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION choregos_utilisateur_visible(uid text) RETURNS boolean AS $$
          SELECT EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = uid)
        $$ LANGUAGE sql STABLE;
        """
    )
    for table, (lecture, ecriture) in POLITIQUES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        check = f" WITH CHECK ({ecriture})" if ecriture else ""
        op.execute(f"CREATE POLICY {table}_org_isolation ON {table} USING ({lecture}){check}")


def downgrade() -> None:
    if not _postgres():
        return
    for table in POLITIQUES:
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS choregos_utilisateur_visible(text)")
    op.execute("DROP FUNCTION IF EXISTS choregos_utilisateur_courant()")
