# SPDX-License-Identifier: Apache-2.0
"""Les connecteurs de l'organisation et la politique de chaque opération (ADR 0034)

Trois tables sous la RLS forcée, comme le reste du registre :
- `org_connectors` : une instance déclarée par l'administrateur de l'organisation ;
- `connector_operations` : ses opérations, et pour chacune la politique (`allowed`, `approval`,
  `forbidden`), les groupes de projets qui y ont droit, le prix, l'empreinte du schéma ;
- `project_operation_policies` : ce qu'un projet resserre — jamais ce qu'il élargit.

Revision ID: d6e8f0a2b4c7
Revises: c5d7e9f1a3b6
Create Date: 2026-10-05 22:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "d6e8f0a2b4c7"
down_revision: str | None = "c5d7e9f1a3b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("org_connectors", "connector_operations", "project_operation_policies")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _horodatage() -> list[sa.Column]:  # type: ignore[type-arg]
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "org_connectors",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("config", Json(), nullable=False),
        sa.Column("secret_refs", Json(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=200), nullable=True),
        *_horodatage(),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_org_connectors_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_org_connectors")),
        sa.UniqueConstraint("org_id", "name", name="uq_org_connectors_org_name"),
    )
    op.create_index(op.f("ix_org_connectors_org_id"), "org_connectors", ["org_id"], unique=False)
    op.create_table(
        "connector_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("connector_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("access", sa.String(length=8), nullable=False),
        sa.Column("policy", sa.String(length=16), nullable=False),
        sa.Column("groups", Json(), nullable=False),
        sa.Column("price_usd", sa.Float(), nullable=True),
        sa.Column("schema_digest", sa.String(length=80), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        *_horodatage(),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], name=op.f("fk_connector_operations_org_id_organizations"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            ["org_connectors.id"],
            name=op.f("fk_connector_operations_connector_id_org_connectors"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_connector_operations")),
        sa.UniqueConstraint("connector_id", "name", name="uq_connector_operations_connector_name"),
    )
    op.create_index(op.f("ix_connector_operations_org_id"), "connector_operations", ["org_id"], unique=False)
    op.create_index(
        op.f("ix_connector_operations_connector_id"), "connector_operations", ["connector_id"], unique=False
    )
    op.create_table(
        "project_operation_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("policy", sa.String(length=16), nullable=False),
        *_horodatage(),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_project_operation_policies_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_project_operation_policies_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["connector_operations.id"],
            name=op.f("fk_project_operation_policies_operation_id_connector_operations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_operation_policies")),
        sa.UniqueConstraint("project_id", "operation_id", name="uq_project_operation_policies"),
    )
    for colonne in ("org_id", "project_id", "operation_id"):
        op.create_index(
            op.f(f"ix_project_operation_policies_{colonne}"), "project_operation_policies", [colonne], unique=False
        )

    if _postgres():
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"""
                CREATE POLICY {table}_org_isolation ON {table}
                USING (
                  '*' = ANY(choregos_current_orgs())
                  OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
                )
                """
            )


def downgrade() -> None:
    if _postgres():
        for table in TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    for colonne in ("operation_id", "project_id", "org_id"):
        op.drop_index(op.f(f"ix_project_operation_policies_{colonne}"), table_name="project_operation_policies")
    op.drop_table("project_operation_policies")
    op.drop_index(op.f("ix_connector_operations_connector_id"), table_name="connector_operations")
    op.drop_index(op.f("ix_connector_operations_org_id"), table_name="connector_operations")
    op.drop_table("connector_operations")
    op.drop_index(op.f("ix_org_connectors_org_id"), table_name="org_connectors")
    op.drop_table("org_connectors")
