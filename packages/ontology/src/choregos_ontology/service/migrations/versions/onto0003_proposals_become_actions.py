# SPDX-License-Identifier: Apache-2.0
"""Action proposals move onto the core's actions: copied, then removed (S20-08)

One schema owner for a governed action: the core (ADR 0035). Each proposal becomes an action of origin
`ontology`, under the SAME id; its core effects are derived from the ontology's action, in its version
(`ontology.effet`, then `ontology.preuve`) — a proposal still pending is therefore decided and executed
like the others. Its approvers become core roles (`owner` → `project_owner`…). A proposal that was
running in the request when the migration ran (`running`, `awaiting_evidence`) has no workflow to
resume: it is closed as `failed`, and says so.

On PostgreSQL the tables are under forced RLS and the migration Job runs as the application's role:
without `set_config('app.current_orgs', '*', true)`, the copy would read nothing, silently.

Revision ID: onto0003
Revises: onto0002
Create Date: 2026-10-06 00:30:00+00:00
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "onto0003"
down_revision: str | None = "onto0002"
branch_labels: str | Sequence[str] | None = None
#: the core's governed actions (S20-01)
depends_on: str | Sequence[str] | None = "f8a0b2c4d6e9"

CORE_ROLE_FOR = {
    "viewer": "developer",
    "contributor": "developer",
    "owner": "project_owner",
    "admin": "org_admin",
}
INTERRUPTED = "interrupted by migration onto0003: it was running in the request, nothing resumes it"


def _json() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _loaded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _instant(value: Any) -> Any:
    """A date read back as text (SQLite) becomes a date again: `bulk_insert` accepts nothing else."""
    return datetime.fromisoformat(value) if isinstance(value, str) else value


ACTIONS = sa.table(
    "actions",
    sa.column("id", sa.String),
    sa.column("org_id", sa.String),
    sa.column("project_id", sa.String),
    sa.column("work_item_id", sa.String),
    sa.column("run_id", sa.String),
    sa.column("origin", sa.String),
    sa.column("kind", sa.String),
    sa.column("title", sa.String),
    sa.column("justification", sa.Text),
    sa.column("params", _json()),
    sa.column("effects", _json()),
    sa.column("proposed_by", _json()),
    sa.column("approval", _json()),
    sa.column("decisions", _json()),
    sa.column("status", sa.String),
    sa.column("result", _json()),
    sa.column("error", sa.Text),
    sa.column("temporal_wf_id", sa.String),
    sa.column("finished_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _effects(ir: dict[str, Any], action_type: str) -> list[dict[str, Any]]:
    action = next((a for a in ir.get("action_types") or [] if a.get("name") == action_type), None) or {}
    count = len(action.get("effects") or [])
    evidence = len(action.get("evidence") or [])
    return [{"effect": "ontology.effet", "with": {"index": i}} for i in range(count)] + [  # lang: fr-ok
        {"effect": "ontology.preuve", "with": {"index": j, "position": count + j}}  # lang: fr-ok
        for j in range(evidence)
    ]


def _approval(previous: dict[str, Any]) -> dict[str, Any]:
    approvers = [
        {"role": CORE_ROLE_FOR.get(str(a.get("role")), "project_owner"), "min": int(a.get("min", 1))}
        for a in previous.get("approvers") or []
    ] or [{"role": "project_owner", "min": 1}]
    return {
        **previous,
        "approvers": approvers,
        "ontology_approvers": list(previous.get("approvers") or []),
    }


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    proposals = connection.execute(
        sa.text(
            "SELECT p.*, pr.org_id AS org_id, v.compiled_ir AS compiled_ir FROM action_proposals p "
            "JOIN projects pr ON pr.id = p.project_id "
            "JOIN ontology_versions v ON v.id = p.version_id"
        )
    ).mappings()
    rows = []
    for p in proposals:
        status = str(p["status"])
        interrupted = status in {"running", "awaiting_evidence"}
        rows.append(
            {
                "id": p["id"],
                "org_id": p["org_id"],
                "project_id": p["project_id"],
                "work_item_id": None,
                "run_id": (_loaded(p["proposed_by"]) or {}).get("run_id"),
                "origin": "ontology",
                "kind": f"ontology.{p['action_type']}"[:128],
                "title": f"{p['action_type']} on {', '.join(_loaded(p['target_ids']) or []) or 'nothing'}"[
                    :300
                ],
                "justification": p["justification"] or None,
                "params": {
                    "ontologie": {  # lang: fr-ok - the persisted key (`actions.PARAMS_KEY`)
                        "version_id": p["version_id"],
                        "action_type": p["action_type"],
                        "target_ids": list(_loaded(p["target_ids"]) or []),
                        "params": dict(_loaded(p["params"]) or {}),
                        "idempotency_key": p["idempotency_key"],
                    }
                },
                "effects": _effects(dict(_loaded(p["compiled_ir"]) or {}), str(p["action_type"])),
                "proposed_by": dict(_loaded(p["proposed_by"]) or {}),
                "approval": _approval(dict(_loaded(p["approval"]) or {})),
                "decisions": list(_loaded(p["decisions"]) or []),
                "status": "failed" if interrupted else status,
                "result": {
                    "effects": list(_loaded(p["effects"]) or []),
                    "evidence": list(_loaded(p["evidence"]) or []),
                },
                "error": INTERRUPTED if interrupted else None,
                "temporal_wf_id": None,
                "finished_at": _instant(p["finished_at"]),
                "created_at": _instant(p["created_at"]),
                "updated_at": _instant(p["updated_at"]),
            }
        )
    if rows:
        op.bulk_insert(ACTIONS, rows)
    if connection.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS action_proposals_org_isolation ON action_proposals")
    op.drop_table("action_proposals")


def downgrade() -> None:
    """The table comes back, and the ontology's actions return to it — their decisions, effects and
    evidence; what Temporal played of them since stays in the core's journal, which is not erased."""
    op.create_table(
        "action_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "version_id",
            sa.String(36),
            sa.ForeignKey("ontology_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action_type", sa.String(63), nullable=False),
        sa.Column("target_ids", _json(), nullable=False),
        sa.Column("params", _json(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("proposed_by", _json(), nullable=False),
        sa.Column("approval", _json(), nullable=False),
        sa.Column("decisions", _json(), nullable=False),
        sa.Column("effects", _json(), nullable=False),
        sa.Column("evidence", _json(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("evidence_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_action_proposals_project_id", "action_proposals", ["project_id"])
    op.create_index("ix_action_proposals_project_status", "action_proposals", ["project_id", "status"])
    columns = ["project_id", "action_type", "idempotency_key"]
    op.create_index("ix_action_proposals_idempotency", "action_proposals", columns)
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    plain = (
        "id", "project_id", "version_id", "action_type", "justification", "status",
        "idempotency_key", "finished_at", "created_at", "updated_at",
    )  # fmt: skip
    as_json = ("target_ids", "params", "proposed_by", "approval", "decisions", "effects", "evidence")
    proposals = sa.table(
        "action_proposals",
        *(sa.column(name) for name in plain),
        *(sa.column(name, _json()) for name in as_json),
    )
    rows = []
    for a in connection.execute(sa.text("SELECT * FROM actions WHERE origin = 'ontology'")).mappings():
        meta = dict((_loaded(a["params"]) or {}).get("ontologie") or {})  # lang: fr-ok - persisted key
        result = dict(_loaded(a["result"]) or {})
        approval = dict(_loaded(a["approval"]) or {})
        rows.append(
            {
                "id": a["id"],
                "project_id": a["project_id"],
                "version_id": meta.get("version_id"),
                "action_type": meta.get("action_type"),
                "target_ids": list(meta.get("target_ids") or []),
                "params": dict(meta.get("params") or {}),
                "justification": a["justification"] or "",
                "status": a["status"] if a["status"] != "approved" else "running",
                "proposed_by": dict(_loaded(a["proposed_by"]) or {}),
                "approval": {**approval, "approvers": approval.get("ontology_approvers") or []},
                "decisions": list(_loaded(a["decisions"]) or []),
                "effects": list(result.get("effects") or []),
                "evidence": list(result.get("evidence") or []),
                "idempotency_key": meta.get("idempotency_key"),
                "finished_at": _instant(a["finished_at"]),
                "created_at": _instant(a["created_at"]),
                "updated_at": _instant(a["updated_at"]),
            }
        )
    if rows:
        op.bulk_insert(proposals, rows)
    op.execute("DELETE FROM actions WHERE origin = 'ontology'")
    if connection.dialect.name != "postgresql":
        return
    op.execute("ALTER TABLE action_proposals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE action_proposals FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY action_proposals_org_isolation ON action_proposals
        USING (
          '*' = ANY(choregos_current_orgs())
          OR choregos_project_org(project_id) = ANY(choregos_current_orgs())
        )
        """
    )
