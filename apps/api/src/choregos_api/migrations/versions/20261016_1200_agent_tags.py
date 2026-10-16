# SPDX-License-Identifier: Apache-2.0
"""Agent tags (S40-10): `agents.tags`, a JSON list of labels people find an agent by.

Tags are metadata, not part of a version. The migration backfills the common (`platform`) agents
from the table below, frozen at the time of the change: a migration never imports the live
catalogue, which keeps changing. Every other agent starts with no tag.

Revision ID: d4f6a8c0e2b3
Revises: b2c4d6e8f0a1
Create Date: 2026-10-16 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from choregos_api.db.base import Json

revision: str = "d4f6a8c0e2b3"
down_revision: str | None = "b2c4d6e8f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: slug -> tags of the built-in catalogue as of 2026-10-16 (frozen on purpose).
CATALOGUE_TAGS: dict[str, list[str]] = {
    "architect": ["madr", "architecture", "design"],
    "agent-drafter": ["agent-authoring", "drafting"],
    "ci-fixer": ["ci", "debugging", "software"],
    "compliance": ["compliance", "licences", "privacy"],
    "developer": ["implementation", "coding", "software"],
    "devops": ["devops", "release", "observability"],
    "docs-writer": ["documentation", "changelog", "software"],
    "knowledge-distiller": ["knowledge", "skills", "distillation"],
    "netops": ["network", "firewall", "infrastructure"],
    "planner": ["planning", "software"],
    "prod-verifier": ["production", "verification", "release"],
    "qa": ["qa", "testing", "mutation-testing"],
    "release-notes-writer": ["release-notes", "documentation", "release"],
    "researcher": ["research", "analysis", "architecture"],
    "review-responder": ["code-review", "software"],
    "reviewer": ["code-review", "quality", "software"],
    "secops": ["security", "supply-chain", "sbom"],
    "security-reviewer": ["security", "code-review"],
    "spec-writer": ["specification", "requirements", "software"],
    "tester": ["testing", "ci", "software"],
    "triager": ["triage", "intake"],
    "ux-designer": ["ux", "accessibility", "design-system"],
    "claude-code": ["mcp-client", "ide"],
    "cursor": ["mcp-client", "ide"],
    "copilot-vscode": ["mcp-client", "ide"],
    "claude-desktop": ["mcp-client", "chat"],
    "claude-ai": ["mcp-client", "chat"],
    "chatgpt": ["mcp-client", "chat"],
}


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("tags", Json(), server_default="[]", nullable=False))
    statement = sa.text("UPDATE agents SET tags = :tags WHERE scope = 'platform' AND slug = :slug").bindparams(
        sa.bindparam("tags", type_=Json())
    )
    for slug, tags in CATALOGUE_TAGS.items():
        op.execute(statement.bindparams(tags=tags, slug=slug))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("tags")
