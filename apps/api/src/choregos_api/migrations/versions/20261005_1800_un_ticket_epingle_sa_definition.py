# SPDX-License-Identifier: Apache-2.0
"""Un projet porte plusieurs workflows ; chaque ticket est épinglé à sa version (ADR 0031)

- `projects.default_workflow` : le nom du workflow par défaut, repris du workflow actif de chaque
  projet ;
- une seule version active PAR NOM (index unique partiel) : avant de le poser, seule la version la
  plus haute du défaut reste active — aucun projet ne gagne un second workflow par la reprise ;
- `work_items.workflow_def_id` reçoit la version que `load_context` rechargerait au prochain
  démarrage : la version active du défaut. Il ne l'écrivait jamais.

Sous PostgreSQL, les tables sont en RLS forcée et le Job de migration tourne sous le rôle de
l'application : sans `set_config('app.current_orgs', '*', true)`, chaque `UPDATE` de reprise toucherait
zéro ligne, en silence.

Revision ID: a7c9e1f3b5d8
Revises: f6b8d0e2a4c6
Create Date: 2026-10-05 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c9e1f3b5d8"
down_revision: str | None = "f6b8d0e2a4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connexion = op.get_bind()
    if connexion.dialect.name == "postgresql":
        connexion.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("default_workflow", sa.String(length=64), nullable=True))

    actives = connexion.execute(
        sa.text(
            "SELECT id, project_id, name, version FROM workflow_defs WHERE is_active "
            "ORDER BY project_id, version DESC"
        )
    ).all()
    garde: dict[str, tuple[str, str]] = {}
    for ident, projet, nom, _version in actives:
        garde.setdefault(projet, (ident, nom))  # la version active la plus haute du projet
    for ident, projet, _nom, _version in actives:
        if garde[projet][0] != ident:
            connexion.execute(
                sa.text("UPDATE workflow_defs SET is_active = :non WHERE id = :id"),
                {"non": False, "id": ident},
            )
    for projet, (ident, nom) in garde.items():
        connexion.execute(
            sa.text("UPDATE projects SET default_workflow = :nom WHERE id = :projet"),
            {"nom": nom, "projet": projet},
        )
        connexion.execute(
            sa.text(
                "UPDATE work_items SET workflow_def_id = :wf WHERE project_id = :projet AND workflow_def_id IS NULL"
            ),
            {"wf": ident, "projet": projet},
        )

    op.create_index(
        "uq_workflow_defs_actif_par_nom",
        "workflow_defs",
        ["project_id", "name"],
        unique=True,
        sqlite_where=sa.text("is_active"),
        postgresql_where=sa.text("is_active"),
    )
    op.create_index("ix_work_items_workflow_def_id", "work_items", ["workflow_def_id"])


def downgrade() -> None:
    op.drop_index("ix_work_items_workflow_def_id", table_name="work_items")
    op.drop_index("uq_workflow_defs_actif_par_nom", table_name="workflow_defs")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("default_workflow")
