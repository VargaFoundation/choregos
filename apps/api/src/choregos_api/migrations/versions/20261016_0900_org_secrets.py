# SPDX-License-Identifier: Apache-2.0
"""Sealed connector secrets: `org_secrets` (ADR 0058)

Values typed in the console, encrypted with an envelope (AES-256-GCM), under forced RLS like the
rest of the registry.

Revision ID: f1b3d5a7c9e2
Revises: 5b7d9f1a3c4e
Create Date: 2026-10-16 09:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'f1b3d5a7c9e2'
down_revision: str | None = '5b7d9f1a3c4e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table('org_secrets',
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('nonce', sa.LargeBinary(), nullable=False),
    sa.Column('wrapped_dek', sa.LargeBinary(), nullable=False),
    sa.Column('dek_nonce', sa.LargeBinary(), nullable=False),
    sa.Column('kek_id', sa.String(length=32), nullable=False),
    sa.Column('algorithm', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.String(length=200), nullable=True),
    sa.Column('updated_by', sa.String(length=200), nullable=True),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_org_secrets_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_org_secrets')),
    sa.UniqueConstraint('org_id', 'name', name='uq_org_secrets_org_name')
    )
    op.create_index(op.f('ix_org_secrets_org_id'), 'org_secrets', ['org_id'], unique=False)

    if _postgres():
        op.execute("ALTER TABLE org_secrets ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE org_secrets FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY org_secrets_org_isolation ON org_secrets
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS org_secrets_org_isolation ON org_secrets")
    op.drop_index(op.f('ix_org_secrets_org_id'), table_name='org_secrets')
    op.drop_table('org_secrets')
