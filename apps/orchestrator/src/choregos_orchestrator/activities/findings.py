"""Activités de triage des findings : dédup, classement, ticket lié, mémoire (docs/plan/05 §5.4)."""

from __future__ import annotations

import math
import re
from typing import Any

from choregos_api.db.models import Finding, WorkItem
from choregos_api.services import persist_event
from choregos_contracts import EventType
from choregos_core import Message, NewItem, utcnow
from sqlalchemy import select
from temporalio import activity

from ..config import get_settings
from .base import db, project_bundle

TOKEN_RE = re.compile(r"[a-zà-ÿ0-9_]+")


def embed(text: str) -> dict[str, float]:
    """Sac de mots normalisé : une « empreinte » suffisante pour la dédup, sans appel modèle.

    L'implémentation Ecphoria remplace ceci par de vrais embeddings (`platform/embed`) ;
    le seuil de la politique s'applique de la même façon.
    """
    tokens = [t for t in TOKEN_RE.findall(text.lower()) if len(t) > 2]
    if not tokens:
        return {}
    counts: dict[str, float] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0.0) + 1.0
    norm = math.sqrt(sum(value * value for value in counts.values()))
    return {token: value / norm for token, value in counts.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    small, large = (a, b) if len(a) <= len(b) else (b, a)
    return sum(value * large.get(token, 0.0) for token, value in small.items())


@activity.defn(name="triage_finding")
async def triage_finding(payload: dict[str, Any]) -> dict[str, Any]:
    """Déduplique, classe, crée le ticket lié et écrit le fait en mémoire."""
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        row = await session.get(Finding, payload["finding_id"])
        if row is None:
            return {"status": "unknown"}
        if row.status != "pending":
            return {"status": row.status, "reused": True}

        threshold = bundle.engine.dedupe_threshold()
        signature = embed(f"{row.title} {row.evidence} {row.suggested_fix or ''}")
        row.embedding = [f"{k}:{v:.4f}" for k, v in sorted(signature.items())[:64]]

        candidates = (
            (
                await session.execute(
                    select(Finding).where(
                        Finding.project_id == bundle.project.id,
                        Finding.id != row.id,
                        Finding.status.in_(["created", "pending"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        for candidate in candidates:
            other = embed(f"{candidate.title} {candidate.evidence} {candidate.suggested_fix or ''}")
            if cosine(signature, other) >= threshold:
                row.status = "duplicate"
                row.duplicate_of = candidate.id
                candidate.occurrences += 1
                origin = (
                    await session.get(WorkItem, candidate.created_work_item_id)
                    if candidate.created_work_item_id
                    else None
                )
                if origin is not None:
                    await bundle.adapters.tracker.comment(
                        origin.tracker_key,
                        f"<!-- choregos:finding-duplicate -->\nOccurrence supplémentaire "
                        f"({candidate.occurrences}) signalée par un agent : {row.evidence}",
                    )
                await persist_event(
                    session,
                    EventType.FINDING_DUPLICATE,
                    project_id=bundle.project.id,
                    project_slug=bundle.slug,
                    subject=row.id,
                    finding_id=row.id,
                    duplicate_of=candidate.id,
                )
                return {"status": "duplicate", "duplicate_of": candidate.id}

        origin = await session.get(WorkItem, row.origin_work_item_id) if row.origin_work_item_id else None
        body = _ticket_body(row, origin, settings.public_url, bundle.slug)
        labels = ["finding", "source:agent", f"type:{row.type}", f"severity:{row.severity}", "needs-triage"]
        if origin is not None:
            labels.append(f"origin:{origin.tracker_key.rsplit('#', 1)[-1]}")
        key = await bundle.adapters.tracker.create_item(
            NewItem(title=f"[{row.type}] {row.title}", body=body, labels=labels)
        )
        created = WorkItem(
            project_id=bundle.project.id,
            tracker_key=key,
            title=row.title,
            body_snapshot=body,
            state="inbox",
            size=row.estimate,
            risk="high" if row.severity == "critical" else "low",
        )
        session.add(created)
        await session.flush()
        row.created_work_item_id = created.id
        row.status = "created"

        if origin is not None:
            await bundle.adapters.tracker.link(key, origin.tracker_key, "origin")
            await bundle.adapters.tracker.comment(
                origin.tracker_key,
                f"<!-- choregos:finding -->\nFinding déposé pendant ce ticket : {key} "
                f"({row.type}, {row.severity}).",
            )
        if bundle.engine.notify_finding(row.severity):
            await bundle.adapters.notify.send(
                bundle.config.notify.slack_channel or "#choregos",
                Message(
                    title=f"Finding {row.severity} — {row.title}",
                    body=row.evidence[:500],
                    url=f"{settings.public_url}/p/{bundle.slug}/findings",
                    severity="error" if row.severity == "critical" else "warning",
                ),
            )
        if bundle.engine.memory_enabled():
            from choregos_core import Fact, Provenance

            await bundle.adapters.memory.write_fact(
                bundle.slug,
                Fact(
                    kind="finding",
                    subject=f"finding:{bundle.slug}:{row.id}",
                    content=f"{row.title} — {row.evidence}",
                    external_id=f"finding:{row.id}",
                    provenance=Provenance(source="agent", run_id=row.origin_run_id),
                ),
            )
        await persist_event(
            session,
            EventType.FINDING_CREATED,
            project_id=bundle.project.id,
            project_slug=bundle.slug,
            subject=row.id,
            finding_id=row.id,
            work_item_key=key,
            severity=row.severity,
        )
        auto = bundle.engine.auto_agent_ready(row.estimate)
        return {"status": "created", "work_item_key": key, "work_item_id": created.id, "agent_ready": auto}


def _ticket_body(row: Finding, origin: WorkItem | None, public_url: str, slug: str) -> str:
    return "\n".join(
        [
            "## Origine",
            f"- Ticket : {origin.tracker_key if origin else '—'}",
            f"- Run : {public_url}/p/{slug}/runs/{row.origin_run_id}" if row.origin_run_id else "- Run : —",
            "",
            "## Problème",
            f"- Type : `{row.type}` · Sévérité : `{row.severity}` · Estimation : `{row.estimate or '?'}`",
            "",
            "## Preuve",
            f"```\n{row.evidence}\n```",
            "",
            "## Correction suggérée",
            row.suggested_fix or "_à qualifier_",
            "",
            "## Pourquoi hors périmètre",
            "Détecté pendant un autre ticket ; corriger ici aurait élargi le périmètre "
            "et rendu la revue plus difficile.",
        ]
    )


@activity.defn(name="finding_quality_ratio")
async def finding_quality_ratio(payload: dict[str, Any]) -> dict[str, Any]:
    """Taux de faux positifs sur 30 jours : au-delà du seuil, le prompt `implement` est à revoir."""
    from datetime import timedelta

    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        since = utcnow() - timedelta(days=30)
        rows = (
            (
                await session.execute(
                    select(Finding).where(
                        Finding.project_id == bundle.project.id, Finding.created_at >= since
                    )
                )
            )
            .scalars()
            .all()
        )
        judged = [row for row in rows if row.relevant is not None]
        if not judged:
            return {"ratio": None, "sample": 0, "alert": False}
        false_positives = sum(1 for row in judged if row.relevant is False)
        ratio = false_positives / len(judged)
        threshold = bundle.policy.findings.false_positive_alert_ratio
        if ratio > threshold:
            await bundle.adapters.notify.send(
                bundle.config.notify.slack_channel or "#choregos",
                Message(
                    title=f"Findings : {ratio:.0%} de faux positifs sur 30 jours",
                    body=f"Seuil {threshold:.0%} dépassé — revoir l'invariant "
                    "« ne signale que ce qui est actionnable ».",
                    severity="warning",
                ),
            )
        return {"ratio": ratio, "sample": len(judged), "alert": ratio > threshold}
