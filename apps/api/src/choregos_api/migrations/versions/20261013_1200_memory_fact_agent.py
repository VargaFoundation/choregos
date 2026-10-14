# SPDX-License-Identifier: Apache-2.0
"""An agent's own memory (S32-02): `memory_facts.agent_slug`

`None` — every existing fact — is the project's shared memory; a slug is that agent's own memory
within the project, read and written only by runs whose agent version says `memory: agent`.

Revision ID: e7a9c1d3f5b7
Revises: 5f7b9d1c3e4a
Create Date: 2026-10-13 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a9c1d3f5b7"
down_revision: str | None = "5f7b9d1c3e4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_facts") as batch:
        batch.add_column(sa.Column("agent_slug", sa.String(length=64), nullable=True))
        batch.create_index(op.f("ix_memory_facts_agent_slug"), ["agent_slug"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("memory_facts") as batch:
        batch.drop_index(op.f("ix_memory_facts_agent_slug"))
        batch.drop_column("agent_slug")
