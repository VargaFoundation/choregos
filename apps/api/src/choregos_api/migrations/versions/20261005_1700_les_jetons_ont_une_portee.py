# SPDX-License-Identifier: Apache-2.0
"""Les jetons d'API ont une portée, et peuvent être liés à un projet (ADR 0030)

`scopes` existait déjà, toujours à `["*"]` et jamais lu. Deux colonnes s'ajoutent :

- `project_id` : un jeton MCP lié à un projet ne voit que lui. La suppression du projet emporte le
  jeton — un jeton qui survivrait à son projet ne servirait plus qu'à se tromper de cible ;
- `last_client` : le client du dernier appel, pour que la page Integrations dise « connecté ».

La RLS de `api_tokens` (0.11) porte sur `user_id` : rien à changer.

Revision ID: f6b8d0e2a4c6
Revises: e5a7c9b1d3f5
Create Date: 2026-10-05 17:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b8d0e2a4c6"
down_revision: str | None = "e5a7c9b1d3f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # `batch_alter_table` : SQLite ne sait pas ajouter une clé étrangère après coup.
    with op.batch_alter_table("api_tokens") as batch:
        batch.add_column(sa.Column("project_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("last_client", sa.String(length=200), nullable=True))
        batch.create_foreign_key(
            "fk_api_tokens_project_id_projects", "projects", ["project_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index("ix_api_tokens_project_id", ["project_id"])


def downgrade() -> None:
    with op.batch_alter_table("api_tokens") as batch:
        batch.drop_index("ix_api_tokens_project_id")
        batch.drop_constraint("fk_api_tokens_project_id_projects", type_="foreignkey")
        batch.drop_column("last_client")
        batch.drop_column("project_id")
