# SPDX-License-Identifier: Apache-2.0
"""Les propositions d'action passent sur les actions du cœur : copiées, puis retirées (S20-08)

Un seul propriétaire de schéma pour une action gouvernée : le cœur (ADR 0035). Chaque proposition
devient une action d'origine `ontology`, sous le MÊME identifiant ; ses effets du cœur se déduisent
de l'action de l'ontologie, dans sa version (`ontology.effet`, puis `ontology.preuve`) — une
proposition encore en attente se décide et s'exécute donc comme les autres. Ses approbateurs passent
en rôles du cœur (`owner` → `project_owner`…). Une proposition qui s'exécutait dans la requête au
moment de la migration (`running`, `awaiting_evidence`) n'a pas de workflow à reprendre : elle est
close en `failed`, et le dit.

Sous PostgreSQL les tables sont en RLS forcée et le Job de migration tourne sous le rôle de
l'application : sans `set_config('app.current_orgs', '*', true)`, la copie ne lirait rien, en silence.

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
#: les actions gouvernées du cœur (S20-01)
depends_on: str | Sequence[str] | None = "f8a0b2c4d6e9"

ROLE_DU_COEUR = {
    "viewer": "developer",
    "contributor": "developer",
    "owner": "project_owner",
    "admin": "org_admin",
}
INTERROMPUE = "interrompue par la migration onto0003 : elle s'exécutait dans la requête, rien ne la reprend"


def _json() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _lu(valeur: Any) -> Any:
    return json.loads(valeur) if isinstance(valeur, str) else valeur


def _instant(valeur: Any) -> Any:
    """Une date relue en texte (SQLite) redevient une date : `bulk_insert` n'accepte qu'elle."""
    return datetime.fromisoformat(valeur) if isinstance(valeur, str) else valeur


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


def _effets(ir: dict[str, Any], action_type: str) -> list[dict[str, Any]]:
    action = next((a for a in ir.get("action_types") or [] if a.get("name") == action_type), None) or {}
    nombre = len(action.get("effects") or [])
    preuves = len(action.get("evidence") or [])
    return [{"effect": "ontology.effet", "with": {"index": i}} for i in range(nombre)] + [
        {"effect": "ontology.preuve", "with": {"index": j, "position": nombre + j}} for j in range(preuves)
    ]


def _approbation(ancienne: dict[str, Any]) -> dict[str, Any]:
    approbateurs = [
        {"role": ROLE_DU_COEUR.get(str(a.get("role")), "project_owner"), "min": int(a.get("min", 1))}
        for a in ancienne.get("approvers") or []
    ] or [{"role": "project_owner", "min": 1}]
    return {
        **ancienne,
        "approvers": approbateurs,
        "ontology_approvers": list(ancienne.get("approvers") or []),
    }


def upgrade() -> None:
    connexion = op.get_bind()
    if connexion.dialect.name == "postgresql":
        connexion.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    propositions = connexion.execute(
        sa.text(
            "SELECT p.*, pr.org_id AS org_id, v.compiled_ir AS compiled_ir FROM action_proposals p "
            "JOIN projects pr ON pr.id = p.project_id "
            "JOIN ontology_versions v ON v.id = p.version_id"
        )
    ).mappings()
    lignes = []
    for p in propositions:
        statut = str(p["status"])
        interrompue = statut in {"running", "awaiting_evidence"}
        lignes.append(
            {
                "id": p["id"],
                "org_id": p["org_id"],
                "project_id": p["project_id"],
                "work_item_id": None,
                "run_id": (_lu(p["proposed_by"]) or {}).get("run_id"),
                "origin": "ontology",
                "kind": f"ontology.{p['action_type']}"[:128],
                "title": f"{p['action_type']} on {', '.join(_lu(p['target_ids']) or []) or 'nothing'}"[:300],
                "justification": p["justification"] or None,
                "params": {
                    "ontologie": {
                        "version_id": p["version_id"],
                        "action_type": p["action_type"],
                        "target_ids": list(_lu(p["target_ids"]) or []),
                        "params": dict(_lu(p["params"]) or {}),
                        "idempotency_key": p["idempotency_key"],
                    }
                },
                "effects": _effets(dict(_lu(p["compiled_ir"]) or {}), str(p["action_type"])),
                "proposed_by": dict(_lu(p["proposed_by"]) or {}),
                "approval": _approbation(dict(_lu(p["approval"]) or {})),
                "decisions": list(_lu(p["decisions"]) or []),
                "status": "failed" if interrompue else statut,
                "result": {
                    "effects": list(_lu(p["effects"]) or []),
                    "evidence": list(_lu(p["evidence"]) or []),
                },
                "error": INTERROMPUE if interrompue else None,
                "temporal_wf_id": None,
                "finished_at": _instant(p["finished_at"]),
                "created_at": _instant(p["created_at"]),
                "updated_at": _instant(p["updated_at"]),
            }
        )
    if lignes:
        op.bulk_insert(ACTIONS, lignes)
    if connexion.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS action_proposals_org_isolation ON action_proposals")
    op.drop_table("action_proposals")


def downgrade() -> None:
    """La table revient, et les actions de l'ontologie y retournent — leurs décisions, leurs effets et
    leurs preuves ; ce que Temporal en a joué depuis reste dans le journal du cœur, qu'on n'efface pas."""
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
    colonnes = ["project_id", "action_type", "idempotency_key"]
    op.create_index("ix_action_proposals_idempotency", "action_proposals", colonnes)
    connexion = op.get_bind()
    if connexion.dialect.name == "postgresql":
        connexion.execute(sa.text("SELECT set_config('app.current_orgs', '*', true)"))
    simples = (
        "id", "project_id", "version_id", "action_type", "justification", "status",
        "idempotency_key", "finished_at", "created_at", "updated_at",
    )  # fmt: skip
    en_json = ("target_ids", "params", "proposed_by", "approval", "decisions", "effects", "evidence")
    propositions = sa.table(
        "action_proposals",
        *(sa.column(nom) for nom in simples),
        *(sa.column(nom, _json()) for nom in en_json),
    )
    lignes = []
    for a in connexion.execute(sa.text("SELECT * FROM actions WHERE origin = 'ontology'")).mappings():
        meta = dict((_lu(a["params"]) or {}).get("ontologie") or {})
        resultat = dict(_lu(a["result"]) or {})
        approbation = dict(_lu(a["approval"]) or {})
        lignes.append(
            {
                "id": a["id"],
                "project_id": a["project_id"],
                "version_id": meta.get("version_id"),
                "action_type": meta.get("action_type"),
                "target_ids": list(meta.get("target_ids") or []),
                "params": dict(meta.get("params") or {}),
                "justification": a["justification"] or "",
                "status": a["status"] if a["status"] != "approved" else "running",
                "proposed_by": dict(_lu(a["proposed_by"]) or {}),
                "approval": {**approbation, "approvers": approbation.get("ontology_approvers") or []},
                "decisions": list(_lu(a["decisions"]) or []),
                "effects": list(resultat.get("effects") or []),
                "evidence": list(resultat.get("evidence") or []),
                "idempotency_key": meta.get("idempotency_key"),
                "finished_at": _instant(a["finished_at"]),
                "created_at": _instant(a["created_at"]),
                "updated_at": _instant(a["updated_at"]),
            }
        )
    if lignes:
        op.bulk_insert(propositions, lignes)
    op.execute("DELETE FROM actions WHERE origin = 'ontology'")
    if connexion.dialect.name != "postgresql":
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
