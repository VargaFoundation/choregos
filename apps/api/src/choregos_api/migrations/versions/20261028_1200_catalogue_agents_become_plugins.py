# SPDX-License-Identifier: Apache-2.0
"""The built-in agents leave the core: their provenance names their plugin (S43-09, ADR 0093 §6)

Until S43-09 a catalogue agent's versions were written by `catalogue:<slug>@<version>` (ADR 0040), with
`!diverged` appended to version 1 when the organisation's text no longer followed the catalogue's
(S39-25). The agents now ship as plugins of `VargaFoundation/choregos-agents`, byte for byte, and the
installer recognises what a plugin wrote by `plugin:<plugin>/<slug>@<version>`. This migration:

1. rewrites every agent version authored `catalogue:<slug>@<rest>` to `plugin:<plugin>/<slug>@<rest>`
   (`<rest>` kept whole: the catalogue version and the `!diverged` mark). Nothing else changes: an agent
   the organisation changed keeps its own versions, so the plugin's upgrade OFFERS the update and never
   overwrites it (ADR 0040 §4);
2. rewrites every skill version a catalogue entry installed (`catalogue:<agent>@…`, the author of its
   agent) to `plugin:<plugin>/<skill>@<plugin version>`, the plugin installer's provenance for a skill;
3. records an `org_plugins` row for every organisation that holds at least one of a plugin's agents or
   skills from the catalogue, at the version these files were taken from (1.0.0, its published content
   digest), listing what it holds: the organisation keeps receiving upgrades through
   `POST /orgs/{org}/plugins/{name}/upgrade`, and the start-up reconcile finds the plugin present.

The mapping below is the published one (`MIGRATION-FROM-CORE.md` of choregos-agents at 45f9af0), written
out rather than read from the offline archives: a migration must mean the same thing when the image
ships later versions. The source recorded is `varga`, the chart's default source of these plugins.

Idempotent: a row already rewritten no longer matches `catalogue:%`, and a plugin row already present
is left alone. Every data step opens every organisation first (`app.current_orgs` = '*').

The downgrade gives the agents of the organisations whose row this migration recorded their
`catalogue:` author back and deletes those rows; what a plugin install wrote stays the plugin's.

Revision ID: b8d0f2a4c6e1
Revises: c9e1a3b5d7f8
Create Date: 2026-10-28 12:00:00+00:00
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from choregos_api.db.base import Json

revision: str = "b8d0f2a4c6e1"
down_revision: str | None = "c9e1a3b5d7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE = "varga"
MARK = "migrated_from"
CORE = ">=0.21.0,<1.0.0"

#: plugin -> (version, content digest, agents, skills, description): the published 1.0.0 releases.
PLUGINS: dict[str, tuple[str, str, tuple[str, ...], tuple[str, ...], str]] = {
    "software-delivery": (
        "1.0.0",
        "sha256:b6b21c798588eafd298380e6c36222e7e4c748f4619f234ad9c72048895aa7b3",
        (
            "architect", "ci-fixer", "compliance", "developer", "devops", "docs-writer", "planner",
            "prod-verifier", "qa", "release-notes-writer", "researcher", "review-responder", "reviewer",
            "secops", "security-reviewer", "spec-writer", "tester", "triager", "ux-designer",
        ),
        ("madr-4",),
        "The agents of a governed software delivery, from the core's built-in catalogue.",
    ),
    "netops": (
        "1.0.0",
        "sha256:999a3452510f45cbc14d299d1e9c23c709c7b19a436a2207511c74b44e4fbe52",
        ("netops",),
        (),
        "Diagnoses network behaviour and proposes every change as code that a person approves.",
    ),
    "authoring": (
        "1.0.0",
        "sha256:d6b9244126c7dad179a3f282a137b04ce713264a0acb4acdd61e0916a407fe4d",
        ("agent-drafter", "knowledge-distiller"),
        (),
        "Agents that author for the organisation: an agent drafter and a knowledge distiller.",
    ),
    "mcp-clients": (
        "1.0.0",
        "sha256:2470e930c1433d783aa3afe664884c22c6a0e907992988071a553551bbd3bdc5",
        ("chatgpt", "claude-ai", "claude-code", "claude-desktop", "copilot-vscode", "cursor"),
        (),
        "Registry entries for the assistants and editors that reach Choregos through its MCP door.",
    ),
}  # fmt: skip


def _plugin_of(slug: str) -> str | None:
    return next((name for name, (_, _, agents, _, _) in PLUGINS.items() if slug in agents), None)


def _open_every_organisation() -> sa.Connection:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        # Forced RLS: the migrating role sees no row without this (S39-27, S40-22e).
        connection.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    return connection


def _rewrite_agents(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text("SELECT id, created_by FROM agent_versions WHERE created_by LIKE 'catalogue:%'")
    ).all()
    for row in rows:
        slug, _, rest = str(row.created_by).removeprefix("catalogue:").partition("@")
        plugin = _plugin_of(slug)
        if plugin is None:
            continue
        connection.execute(
            sa.text("UPDATE agent_versions SET created_by = :author WHERE id = :id"),
            {"author": f"plugin:{plugin}/{slug}@{rest}", "id": row.id},
        )


def _rewrite_skills(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT v.id AS id, v.created_by AS created_by, s.slug AS skill FROM skill_versions v "
            "JOIN skills s ON s.id = v.skill_id WHERE v.created_by LIKE 'catalogue:%'"
        )
    ).all()
    for row in rows:
        agent = str(row.created_by).removeprefix("catalogue:").partition("@")[0]
        plugin = _plugin_of(agent)
        if plugin is None or row.skill not in PLUGINS[plugin][3]:
            continue
        connection.execute(
            sa.text("UPDATE skill_versions SET created_by = :author WHERE id = :id"),
            {"author": f"plugin:{plugin}/{row.skill}@{PLUGINS[plugin][0]}", "id": row.id},
        )


def _held(connection: sa.Connection, org_id: str, table: str, versions: str, key: str, plugin: str) -> list[str]:
    """The agents (or skills) of `plugin` whose version 1 the plugin's provenance wrote, in `org_id`."""
    found = connection.execute(
        sa.text(
            f"SELECT DISTINCT t.slug AS slug FROM {table} t JOIN {versions} v ON v.{key} = t.id "  # noqa: S608
            "WHERE t.org_id = :org AND v.version = 1 AND v.created_by LIKE :prefix ORDER BY t.slug"
        ),
        {"org": org_id, "prefix": f"plugin:{plugin}/%"},
    ).scalars()
    return [str(slug) for slug in found]


def _record_plugins(connection: sa.Connection) -> None:
    insert = sa.text(
        "INSERT INTO org_plugins (id, org_id, name, source, version, digest, manifest, contents, details, "
        "status, is_default, installed_by, installed_at, created_at, updated_at) VALUES (:id, :org, :name, "
        ":source, :version, :digest, :manifest, :contents, :details, 'installed', :default, NULL, :now, "
        ":now, :now)"
    ).bindparams(
        sa.bindparam("manifest", type_=Json()),
        sa.bindparam("contents", type_=Json()),
        sa.bindparam("details", type_=Json()),
        sa.bindparam("default", type_=sa.Boolean()),
        sa.bindparam("now", type_=sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    for org_id in connection.execute(sa.text("SELECT id FROM organizations ORDER BY id")).scalars():
        for name, (version, digest, agents, skills, description) in PLUGINS.items():
            present = connection.execute(
                sa.text("SELECT 1 FROM org_plugins WHERE org_id = :org AND name = :name"),
                {"org": org_id, "name": name},
            ).first()
            if present is not None:
                continue
            held_agents = [s for s in _held(connection, org_id, "agents", "agent_versions", "agent_id", name)
                           if s in agents]  # fmt: skip
            held_skills = [s for s in _held(connection, org_id, "skills", "skill_versions", "skill_id", name)
                           if s in skills]  # fmt: skip
            if not held_agents and not held_skills:
                continue
            manifest = {
                "apiVersion": "choregos/v1",
                "kind": "Plugin",
                "metadata": {
                    "name": name,
                    "version": version,
                    "publisher": "Varga Foundation",
                    "license": "Apache-2.0",
                    "description": description,
                    "core": CORE,
                },
                "requires": {"plugins": [], "connectors": {}},
                "contents": {
                    "agents": [f"agents/{a}.yaml" for a in agents],
                    "skills": [f"skills/{s}" for s in skills],
                },
            }
            contents = {
                "agents": held_agents,
                "skills": held_skills,
                "templates": [],
                "evals": [],
                "procedures": [],
                "servers": [],
            }
            details = {
                "templates": {},
                "procedures": {},
                "servers": {},
                "skipped": {"skills": [], "agents": []},
                MARK: "catalogue",
            }
            connection.execute(
                insert,
                {
                    "id": str(uuid.uuid4()),
                    "org": org_id,
                    "name": name,
                    "source": SOURCE,
                    "version": version,
                    "digest": digest,
                    "manifest": manifest,
                    "contents": contents,
                    "details": details,
                    "default": False,
                    "now": now,
                },
            )


def upgrade() -> None:
    connection = _open_every_organisation()
    _rewrite_agents(connection)
    _rewrite_skills(connection)
    _record_plugins(connection)


def downgrade() -> None:
    connection = _open_every_organisation()
    rows = connection.execute(
        sa.text("SELECT id, org_id, name, details FROM org_plugins WHERE installed_by IS NULL")
    ).all()
    for row in rows:
        details = row.details if isinstance(row.details, dict) else json.loads(row.details or "{}")
        if details.get(MARK) != "catalogue" or row.name not in PLUGINS:
            continue
        versions = connection.execute(
            sa.text(
                "SELECT v.id AS id, v.created_by AS created_by FROM agent_versions v "
                "JOIN agents a ON a.id = v.agent_id WHERE a.org_id = :org AND v.created_by LIKE :prefix"
            ),
            {"org": row.org_id, "prefix": f"plugin:{row.name}/%"},
        ).all()
        for version in versions:
            rest = str(version.created_by).removeprefix(f"plugin:{row.name}/")
            connection.execute(
                sa.text("UPDATE agent_versions SET created_by = :author WHERE id = :id"),
                {"author": f"catalogue:{rest}", "id": version.id},
            )
        connection.execute(sa.text("DELETE FROM org_plugins WHERE id = :id"), {"id": row.id})
