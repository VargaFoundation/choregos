# SPDX-License-Identifier: Apache-2.0
"""Backfill the catalogue provenance of agents installed by a project template (S39-25, #674)

A template that listed `catalogue:<slug>` used to install the agent authored `template:<name>@<v>` and
scoped `org`, so the catalogue's upgrade refused it ("does not come from the catalogue"). New imports
now record the provenance of a direct install; this migration does the same for the agents already
there: version 1 authored `template:*`, slug in the catalogue, not a fork, scope `org`.

- the latest version is the catalogue's text: version 1 becomes `catalogue:<slug>@<catalogue version>`;
- it is not (the organisation published its own text since): the provenance is recorded and the agent
  marked `!diverged`, so the upgrade proposes and never publishes over it. The catalogue version it came
  from is the current one when version 1 still equals it, `0` (unknown) otherwise.

The agent becomes a `platform` agent, like a direct install (ADR 0061). Idempotent; no downgrade (the
`template:*` author is lost on purpose: the audit keeps the template as `via`).

Revision ID: f3a5c7e9b1d2
Revises: d4f6a8c0e2b4
Create Date: 2026-10-16 15:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a5c7e9b1d2"
down_revision: str | None = "d4f6a8c0e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Imported here: the migration reads the catalogue of the release that runs it.
    from choregos_api.schemas import AgentSpec
    from choregos_api.services.agents import empreinte
    from choregos_core.catalogue_d_agents import entrees

    connection = op.get_bind()
    for entry in entrees():
        checksum = empreinte(AgentSpec.model_validate(entry.document.get("spec") or {}))
        rows = connection.execute(
            sa.text(
                "SELECT a.id AS id, v.id AS first_id, v.checksum AS first_checksum FROM agents a "
                "JOIN agent_versions v ON v.agent_id = a.id AND v.version = 1 "
                "WHERE a.slug = :slug AND a.scope = 'org' AND a.forked_from_agent_id IS NULL "
                "AND v.created_by LIKE 'template:%'"
            ),
            {"slug": entry.slug},
        ).all()
        for row in rows:
            latest = connection.execute(
                sa.text("SELECT checksum FROM agent_versions WHERE agent_id = :id ORDER BY version DESC LIMIT 1"),
                {"id": row.id},
            ).scalar_one()
            if latest == checksum:
                author = f"catalogue:{entry.slug}@{entry.version}"
            else:
                known = row.first_checksum == checksum
                author = f"catalogue:{entry.slug}@{entry.version if known else 0}!diverged"
            connection.execute(
                sa.text("UPDATE agent_versions SET created_by = :author WHERE id = :id"),
                {"author": author, "id": row.first_id},
            )
            connection.execute(sa.text("UPDATE agents SET scope = 'platform' WHERE id = :id"), {"id": row.id})


def downgrade() -> None:
    """Nothing to undo: the data was only made more precise."""
