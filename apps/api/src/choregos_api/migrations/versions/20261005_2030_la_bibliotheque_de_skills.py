# SPDX-License-Identifier: Apache-2.0
"""La bibliothèque de skills : des skills de l'organisation et leurs versions immuables (ADR 0033)

Sous la RLS forcée, comme le registre d'agents : une skill de `b` n'existe pas pour `a`.

Revision ID: f2a4b6c8d0e3
Revises: e1f3a5b7c9d2
Create Date: 2026-10-05 20:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = 'f2a4b6c8d0e3'
down_revision: str | None = 'e1f3a5b7c9d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLES = ("skills", "skill_versions")


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table('skills',
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('slug', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_skills_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_skills')),
    sa.UniqueConstraint('org_id', 'slug', name='uq_skills_org_slug')
    )
    op.create_index(op.f('ix_skills_org_id'), 'skills', ['org_id'], unique=False)
    op.create_table('skill_versions',
    sa.Column('skill_id', sa.String(length=36), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('files', Json(), nullable=False),
    sa.Column('digest', sa.String(length=80), nullable=False),
    sa.Column('created_by', sa.String(length=200), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_skill_versions_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['skills.id'], name=op.f('fk_skill_versions_skill_id_skills'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_skill_versions')),
    sa.UniqueConstraint('skill_id', 'version', name='uq_skill_versions_skill_version')
    )
    op.create_index(op.f('ix_skill_versions_org_id'), 'skill_versions', ['org_id'], unique=False)
    op.create_index(op.f('ix_skill_versions_skill_id'), 'skill_versions', ['skill_id'], unique=False)


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
    op.drop_index(op.f('ix_skill_versions_skill_id'), table_name='skill_versions')
    op.drop_index(op.f('ix_skill_versions_org_id'), table_name='skill_versions')
    op.drop_table('skill_versions')
    op.drop_index(op.f('ix_skills_org_id'), table_name='skills')
    op.drop_table('skills')
