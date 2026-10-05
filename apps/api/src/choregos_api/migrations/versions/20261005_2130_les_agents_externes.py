# SPDX-License-Identifier: Apache-2.0
"""Les agents externes : un jeton de la porte MCP ou un client OAuth, rattaché à un agent (ADR 0033)

Sous la RLS forcée, comme le reste du registre.

Revision ID: b4c6d8e0f2a5
Revises: a3b5c7d9e1f4
Create Date: 2026-10-05 21:30:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'b4c6d8e0f2a5'
down_revision: str | None = 'a3b5c7d9e1f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table('agent_credentials',
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('agent_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('api_token_id', sa.String(length=36), nullable=True),
    sa.Column('client_id', sa.String(length=200), nullable=True),
    sa.Column('created_by', sa.String(length=200), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], name=op.f('fk_agent_credentials_agent_id_agents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['api_token_id'], ['api_tokens.id'], name=op.f('fk_agent_credentials_api_token_id_api_tokens'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_agent_credentials_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_credentials')),
    sa.UniqueConstraint('api_token_id', name='uq_agent_credentials_api_token_id'),
    sa.UniqueConstraint('org_id', 'client_id', name='uq_agent_credentials_org_client')
    )
    op.create_index(op.f('ix_agent_credentials_agent_id'), 'agent_credentials', ['agent_id'], unique=False)
    op.create_index(op.f('ix_agent_credentials_org_id'), 'agent_credentials', ['org_id'], unique=False)


    if _postgres():
        op.execute("ALTER TABLE agent_credentials ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE agent_credentials FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY agent_credentials_org_isolation ON agent_credentials
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS agent_credentials_org_isolation ON agent_credentials")
    op.drop_index(op.f('ix_agent_credentials_org_id'), table_name='agent_credentials')
    op.drop_index(op.f('ix_agent_credentials_agent_id'), table_name='agent_credentials')
    op.drop_table('agent_credentials')
