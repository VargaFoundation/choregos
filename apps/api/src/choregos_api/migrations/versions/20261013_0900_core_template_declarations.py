# SPDX-License-Identifier: Apache-2.0
"""Stored copies of the core templates get the declarations agents actually produce (S31-02)

Every declared output is now required. The core templates (`default-simple`, `full-auto`,
`advanced`) declared outputs nothing produces (`spec`, `plan`, `branch`, `commits`, `duplicate_of`,
`allowed_paths`) and were fixed; the copies projects store are not touched by that fix. A copy equal,
metadata aside, to an old core template is upgraded here: its YAML keeps its comments, only the
declaration lines change. A copy a person modified is left alone and a warning names its legacy
declarations; the engine does not require those outputs (`choregos_core.dsl.legacy`).

Under PostgreSQL the tables are under forced RLS and the migration job runs as the application role:
without `set_config('app.current_orgs', '*', true)` every UPDATE would touch zero rows, silently.

No downgrade: the old declarations named outputs nothing produced.

Revision ID: c5e7a9b1d3f5
Revises: b4d6f8a0c2e4
Create Date: 2026-10-13 09:00:00+00:00
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "c5e7a9b1d3f5"
down_revision: str | None = "b4d6f8a0c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

WORKFLOW_DEFS = sa.table(
    "workflow_defs",
    sa.column("id", sa.String),
    sa.column("project_id", sa.String),
    sa.column("name", sa.String),
    sa.column("version", sa.Integer),
    sa.column("yaml", sa.Text),
    sa.column("json", Json()),
    sa.column("checksum", sa.String),
)


def upgrade_rows(connection: Any) -> tuple[list[str], list[str]]:
    """Upgrades the unmodified copies; returns (upgraded ids, warnings for modified copies)."""
    from choregos_contracts import Workflow
    from choregos_core.dsl.legacy import legacy_declarations, upgrade_core_copy
    from choregos_core.dsl.parser import checksum

    upgraded: list[str] = []
    warnings: list[str] = []
    for row in connection.execute(sa.select(WORKFLOW_DEFS)).mappings().all():
        document = row["json"] or {}
        if not isinstance(document, dict) or not document.get("transitions"):
            continue
        result = upgrade_core_copy(document, row["yaml"] or "")
        if result is not None:
            text, new, template = result
            connection.execute(
                WORKFLOW_DEFS.update()
                .where(WORKFLOW_DEFS.c.id == row["id"])
                .values(yaml=text, json=new, checksum=checksum(Workflow.model_validate(new)))
            )
            upgraded.append(row["id"])
            log.info("workflow %s v%s: upgraded from the old %s declarations", row["name"], row["version"], template)
            continue
        legacy = legacy_declarations(document)
        if legacy:
            message = (
                f"workflow {row['name']} v{row['version']} (project {row['project_id']}) was modified from a core "
                f"template and keeps declarations nothing produces ({', '.join(legacy)}): they stay not required; "
                "remove them, or declare `spec_markdown` / `plan_markdown`"
            )
            log.warning(message)
            warnings.append(message)
    return upgraded, warnings


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    upgrade_rows(connection)


def downgrade() -> None:
    """Nothing to undo: the old declarations named outputs nothing produced."""
