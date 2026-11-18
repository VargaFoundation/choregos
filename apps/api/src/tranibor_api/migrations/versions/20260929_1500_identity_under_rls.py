# SPDX-License-Identifier: Apache-2.0
"""Identity under RLS: organisations, memberships, users, API tokens

ADR 0026 left these four tables outside RLS: the principal was resolved BEFORE the scope was
set, and a policy would have blinded it. Since 0.10.1, identity resolves into a platform
scope (`deps._principal_from_user`), and the whole API suite runs under RLS in CI. The
condition is lifted; ADR 0026 is updated.

- `organizations`: visible if its slug is in the scope;
- `memberships`: visible if its organisation is;
- `users`: visible if the user belongs to an organisation of the scope, or if it is
  the caller itself (`app.current_user`): a user without a membership must be able to read itself.
  Creating a user stays allowed (`WITH CHECK true`): the e-mail is unique across the
  instance, and an invitation creates the user BEFORE its membership;
- `api_tokens`: those of a visible user, or of the caller.

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

WHOLE = "'*' = ANY(choregos_current_orgs())"
POLICIES = {
    "organizations": (f"{WHOLE} OR slug = ANY(choregos_current_orgs())", None),
    "memberships": (f"{WHOLE} OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())", None),
    "users": (f"{WHOLE} OR id = choregos_utilisateur_courant() OR choregos_utilisateur_visible(id)", "true"),
    "api_tokens": (
        f"{WHOLE} OR user_id = choregos_utilisateur_courant() OR choregos_utilisateur_visible(user_id)",
        f"{WHOLE} OR user_id = choregos_utilisateur_courant()",
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
    # Rights of the CALLER: `memberships` is itself under RLS, the sub-select sees only the
    # memberships of the scope.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION choregos_utilisateur_visible(uid text) RETURNS boolean AS $$
          SELECT EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = uid)
        $$ LANGUAGE sql STABLE;
        """
    )
    for table, (read_clause, write_clause) in POLICIES.items():
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        check = f" WITH CHECK ({write_clause})" if write_clause else ""
        op.execute(f"CREATE POLICY {table}_org_isolation ON {table} USING ({read_clause}){check}")


def downgrade() -> None:
    if not _postgres():
        return
    for table in POLICIES:
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS choregos_utilisateur_visible(text)")
    op.execute("DROP FUNCTION IF EXISTS choregos_utilisateur_courant()")
