# SPDX-License-Identifier: Apache-2.0
"""Per-project enablement of organisation connectors (ADR 0074)

`org_connectors.availability` (`all_projects` by default: nothing changes for existing connectors)
and `org_connector_projects`, the projects a `selected_projects` connector is turned on for, under
forced RLS like the rest of the registry.

Revision ID: 9d2e4f6a8b1c
Revises: a8c0e2b4d6f8
Create Date: 2026-10-17 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '9d2e4f6a8b1c'
down_revision: str | None = 'a8c0e2b4d6f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table('org_connectors') as batch:
        batch.add_column(
            sa.Column('availability', sa.String(length=24), server_default='all_projects', nullable=False)
        )
    op.create_table('org_connector_projects',
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('connector_id', sa.String(length=36), nullable=False),
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('enabled_by', sa.String(length=200), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['org_connectors.id'], name=op.f('fk_org_connector_projects_connector_id_org_connectors'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_org_connector_projects_org_id_organizations'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name=op.f('fk_org_connector_projects_project_id_projects'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_org_connector_projects')),
    sa.UniqueConstraint('connector_id', 'project_id', name='uq_org_connector_projects')
    )
    op.create_index(op.f('ix_org_connector_projects_org_id'), 'org_connector_projects', ['org_id'], unique=False)
    op.create_index(op.f('ix_org_connector_projects_connector_id'), 'org_connector_projects', ['connector_id'], unique=False)
    op.create_index(op.f('ix_org_connector_projects_project_id'), 'org_connector_projects', ['project_id'], unique=False)

    if _postgres():
        op.execute("ALTER TABLE org_connector_projects ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE org_connector_projects FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY org_connector_projects_org_isolation ON org_connector_projects
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS org_connector_projects_org_isolation ON org_connector_projects")
    op.drop_index(op.f('ix_org_connector_projects_project_id'), table_name='org_connector_projects')
    op.drop_index(op.f('ix_org_connector_projects_connector_id'), table_name='org_connector_projects')
    op.drop_index(op.f('ix_org_connector_projects_org_id'), table_name='org_connector_projects')
    op.drop_table('org_connector_projects')
    with op.batch_alter_table('org_connectors') as batch:
        batch.drop_column('availability')
