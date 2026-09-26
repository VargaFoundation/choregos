"""L'audit appartient à une organisation

`audit_log` était la dernière table de mutation hors RLS, et elle n'avait même pas de colonne
d'organisation. `GET /api/v1/audit` n'appliquait aucun filtre : il suffisait d'avoir
`audit:read` dans UNE organisation pour lire l'audit de TOUTES — les slugs de projets, les
actions, les identifiants d'acteurs de tous les locataires de l'instance. L'état des lieux du
2026-09-24 avait manqué ce point ; il est sorti de l'inventaire du 2026-09-26.

`org_id` est nullable à dessein : une connexion ou un jeton d'API n'appartient à aucune
organisation. La politique laisse donc passer ces lignes **uniquement** pour une session de
portée `*` (plateforme, orchestrateur, jeton de run) — fail-closed comme les douze autres.

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
    # `batch_alter_table` parce que SQLite ne sait pas ajouter une contrainte après coup : il
    # recrée la table. Le test `test_migrations` compare les modèles aux migrations sur SQLite,
    # et une clé étrangère posée seulement sous PostgreSQL l'a fait rougir à raison.
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
    # `USING` borne la LECTURE, `WITH CHECK` borne l'ÉCRITURE, et les deux diffèrent ici.
    # Sans `WITH CHECK` explicite, PostgreSQL réutilise `USING` à l'INSERT — et la connexion
    # elle-même devenait impossible : `auth.login` écrit une trace SANS organisation (elle
    # précède la résolution), depuis une session déjà bornée aux organisations du principal.
    # Le premier jet de cette migration a fait échouer tous les tests d'API sur
    # « new row violates row-level security policy for table audit_log ». Une session bornée
    # peut donc ÉCRIRE une trace de plateforme — qu'elle ne pourra pas relire — mais jamais
    # une trace attribuée à une AUTRE organisation.
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
