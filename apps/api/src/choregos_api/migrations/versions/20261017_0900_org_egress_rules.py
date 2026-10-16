# SPDX-License-Identifier: Apache-2.0
"""An organisation's egress allowlist: `org_egress_rules` (S39-22a, ADR 0066)

The table, one live row per host, forced RLS like `org_secrets`, and a data step that seeds an
`allowed` rule for every host the organisation already calls, so enforcing the list breaks nothing
on upgrade: the hosts of its org connectors, of its web and git knowledge sources, and of its skill
sources. The seed is pure SQL plus a URL parser inlined here: it must not import application code,
which can change later.

Revision ID: d4f6a8c0e2b4
Revises: b2c4d6e8f0a1
Create Date: 2026-10-17 09:00:00+00:00
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import op

revision: str = "d4f6a8c0e2b4"
down_revision: str | None = "b2c4d6e8f0a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Configuration fields that hold a URL, in connectors and sources.
URL_FIELDS = ("url", "base_url", "graph_url", "login_url", "api_url")
GITHUB_HOSTS = ("api.github.com", "codeload.github.com")
DEFAULT_SKILL_SOURCES = ("github.com/anthropics/skills",)


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _host_port(url: Any) -> tuple[str, int] | None:
    if not isinstance(url, str) or "://" not in url:
        return None
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port or (80 if parts.scheme == "http" else 443)
    except ValueError:
        return None
    if not host or parts.scheme not in ("http", "https"):
        return None
    return host, port


def _urls_of(config: Any) -> list[tuple[str, int]]:
    config = _json(config)
    if not isinstance(config, dict):
        return []
    found = [_host_port(config.get(field)) for field in URL_FIELDS]
    return [hp for hp in found if hp]


def _seed() -> None:
    bind = op.get_bind()
    # (org_id, host) -> [ports, used_by, reason]
    wanted: dict[tuple[str, str], list[Any]] = {}

    def want(org_id: str, host: str, port: int, used_by: tuple[str, ...], reason: str) -> None:
        entry = wanted.setdefault((org_id, host), [set(), set(), reason])
        entry[0].add(port)
        entry[1].update(used_by)

    both = ("api", "workers")
    for org_id, name, ctype, config in bind.execute(
        sa.text("SELECT org_id, name, type, config FROM org_connectors")
    ):
        reason = f"seeded from the connector {name}"
        for host, port in _urls_of(config):
            want(org_id, host, port, both, reason)
        parsed = _json(config)
        if ctype == "sharepoint":
            # Graph defaults, and where a download is redirected to
            for host in ("graph.microsoft.com", "login.microsoftonline.com", "*.sharepoint.com"):
                want(org_id, host, 443, both, reason)
        if ctype == "entra":
            for host in ("graph.microsoft.com", "login.microsoftonline.com"):
                want(org_id, host, 443, both, reason)
        if ctype == "confluence" and isinstance(parsed, dict) and parsed.get("auth") == "scoped":
            want(org_id, "api.atlassian.com", 443, both, reason)
    for org_id, kind, config in bind.execute(sa.text("SELECT org_id, kind, config FROM knowledge_sources")):
        reason = f"seeded from a {kind} knowledge source"
        parsed = _json(config)
        if kind in ("web", "git"):
            for host, port in _urls_of(config):
                want(org_id, host, port, both, reason)
        elif kind == "sharepoint":
            want(org_id, "*.sharepoint.com", 443, both, reason)
        elif kind == "confluence" and isinstance(parsed, dict) and parsed.get("include_attachments"):
            want(org_id, "api.media.atlassian.com", 443, both, reason)
    for org_id, settings in bind.execute(sa.text("SELECT id, settings FROM organizations")):
        settings = _json(settings) or {}
        written = settings.get("skill_sources") if isinstance(settings, dict) else None
        sources = written if isinstance(written, list) else list(DEFAULT_SKILL_SOURCES)
        for source in sources:
            host = str(source).split("/", 1)[0].lower()
            if host == "github.com":
                want(org_id, "github.com", 443, ("api",), "seeded from the skill sources")
                for extra in GITHUB_HOSTS:
                    want(org_id, extra, 443, ("api",), "seeded from the skill sources")
            elif host == "gitlab.com":
                want(org_id, "gitlab.com", 443, ("api",), "seeded from the skill sources")

    table = sa.table(
        "org_egress_rules",
        sa.column("id"),
        sa.column("org_id"),
        sa.column("host"),
        sa.column("ports", sa.JSON),
        sa.column("used_by", sa.JSON),
        sa.column("status"),
        sa.column("reason"),
        sa.column("origin"),
        sa.column("requested_by"),
        sa.column("requested_at", sa.DateTime(timezone=True)),
        sa.column("decided_by"),
        sa.column("decided_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    rows = [
        {
            "id": str(uuid.uuid4()),
            "org_id": org_id,
            "host": host,
            "ports": sorted(ports),
            "used_by": sorted(used_by),
            "status": "allowed",
            "reason": reason,
            "origin": "migration",
            "requested_by": "platform",
            "requested_at": now,
            "decided_by": "platform",
            "decided_at": now,
        }
        for (org_id, host), (ports, used_by, reason) in sorted(wanted.items())
    ]
    if rows:
        op.bulk_insert(table, rows)


def upgrade() -> None:
    op.create_table('org_egress_rules',
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=False),
    sa.Column('ports', sa.JSON(), nullable=False),
    sa.Column('used_by', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('origin', sa.String(length=200), nullable=False),
    sa.Column('requested_by', sa.String(length=320), nullable=True),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decided_by', sa.String(length=320), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('decision_note', sa.String(length=500), nullable=True),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name=op.f('fk_org_egress_rules_org_id_organizations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_org_egress_rules'))
    )
    op.create_index(op.f('ix_org_egress_rules_org_id'), 'org_egress_rules', ['org_id'], unique=False)
    op.create_index(
        'uq_org_egress_rules_live_host',
        'org_egress_rules',
        ['org_id', 'host'],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'allowed')"),
        sqlite_where=sa.text("status IN ('requested', 'allowed')"),
    )

    if _postgres():
        op.execute("ALTER TABLE org_egress_rules ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE org_egress_rules FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY org_egress_rules_org_isolation ON org_egress_rules
            USING (
              '*' = ANY(choregos_current_orgs())
              OR choregos_org_slug(org_id) = ANY(choregos_current_orgs())
            )
            """
        )
    _seed()


def downgrade() -> None:
    if _postgres():
        op.execute("DROP POLICY IF EXISTS org_egress_rules_org_isolation ON org_egress_rules")
    op.drop_index('uq_org_egress_rules_live_host', table_name='org_egress_rules')
    op.drop_index(op.f('ix_org_egress_rules_org_id'), table_name='org_egress_rules')
    op.drop_table('org_egress_rules')
