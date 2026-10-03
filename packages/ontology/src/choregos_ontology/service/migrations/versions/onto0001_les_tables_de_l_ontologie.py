# SPDX-License-Identifier: Apache-2.0
"""Les tables de l'ontologie : versions compilées et objets des datasources `table`, sous RLS

Branche `ontology` du greffon de l'essai : elle dépend de la tête du cœur et ne touche à aucune
table du cœur. Sur PostgreSQL, les deux tables sont sous RLS forcée, rattachées à l'organisation
par leur projet — la même politique que les tables du cœur.

Revision ID: onto0001
Revises:
Create Date: 2026-10-03 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "onto0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("ontology",)
depends_on: str | Sequence[str] | None = "e5a7c9b1d3f5"

TABLES = ("ontology_versions", "managed_objects")


def _json() -> sa.types.TypeEngine[object]:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ontology_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(63), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("checksum", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("compiled_ir", _json(), nullable=False),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_ontology_versions_project_id", "ontology_versions", ["project_id"])
    op.create_index("ix_ontology_versions_project_status", "ontology_versions", ["project_id", "status"])
    op.create_table(
        "managed_objects",
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("object_type", sa.String(63), primary_key=True),
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("properties", _json(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE INDEX ix_managed_objects_properties ON managed_objects USING gin (properties)")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_org_isolation ON {table}
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_project_org(project_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
    op.drop_table("managed_objects")
    op.drop_index("ix_ontology_versions_project_status", table_name="ontology_versions")
    op.drop_index("ix_ontology_versions_project_id", table_name="ontology_versions")
    op.drop_table("ontology_versions")
