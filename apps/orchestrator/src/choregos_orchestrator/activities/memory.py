"""Activités mémoire : ingestion idempotente, context pack, écriture gouvernée."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_api.db.models import Finding, Release, Run, WorkItem
from choregos_core import Fact, Message, Provenance, utcnow
from sqlalchemy import select
from temporalio import activity

from .base import db, project_bundle


@activity.defn(name="ingest_sources")
async def ingest_sources(payload: dict[str, Any]) -> dict[str, Any]:
    """Construit des faits depuis l'état de la plateforme et les pousse en upsert idempotent."""
    sources: list[str] = payload.get("sources", [])
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        if not bundle.engine.memory_enabled():
            return {"facts": 0, "skipped": "mémoire désactivée"}
        events: list[dict[str, Any]] = []

        if "closed_issues" in sources:
            since = utcnow() - timedelta(days=365)
            items = (
                (
                    await session.execute(
                        select(WorkItem).where(
                            WorkItem.project_id == bundle.project.id,
                            WorkItem.closed_at.is_not(None),
                            WorkItem.closed_at >= since,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for item in items:
                cost = float((item.totals or {}).get("cost_usd", 0.0))
                events.append(
                    {
                        "external_id": f"workitem:{item.tracker_key}",
                        "kind": "ticket_summary",
                        "subject": f"ticket_summary:{item.tracker_key}",
                        "content": f"{item.title} — état final {item.state}, {cost:.2f} USD.",
                        "source": "tracker",
                    }
                )

        if "run_lessons" in sources:
            runs = (
                (
                    await session.execute(
                        select(Run).where(Run.project_id == bundle.project.id, Run.status == "failed")
                    )
                )
                .scalars()
                .all()
            )
            for run in runs:
                reason = (run.result or {}).get("reason") or "échec"
                events.append(
                    {
                        "external_id": f"run:{run.id}",
                        "kind": "run_lesson",
                        "subject": f"run_lesson:{bundle.slug}:{run.stage_role}",
                        "content": f"L'étape {run.stage_role} a échoué ({reason}) — tentative {run.attempt}.",
                        "source": "orchestrator",
                    }
                )

        if "deployments" in sources:
            releases = (
                (
                    await session.execute(
                        select(Release).where(
                            Release.project_id == bundle.project.id,
                            Release.status.in_(["done", "rolled_back"]),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for release in releases:
                kind = "incident" if release.status == "rolled_back" else "ticket_summary"
                events.append(
                    {
                        "external_id": f"release:{release.id}",
                        "kind": kind,
                        "subject": f"{kind}:{bundle.slug}:{release.env}:{release.batch_no}",
                        "content": (
                            f"Lot {release.batch_no} sur {release.env} : {release.status} "
                            f"({len(release.items)} ticket(s)). {release.notes or ''}"
                        ).strip(),
                        "source": "cd",
                    }
                )

        if "findings" in sources:
            findings = (
                (
                    await session.execute(
                        select(Finding).where(
                            Finding.project_id == bundle.project.id, Finding.status == "created"
                        )
                    )
                )
                .scalars()
                .all()
            )
            for finding in findings:
                events.append(
                    {
                        "external_id": f"finding:{finding.id}",
                        "kind": "finding",
                        "subject": f"finding:{bundle.slug}:{finding.id}",
                        "content": f"{finding.title} — {finding.evidence}",
                        "source": "agent",
                    }
                )

        if events:
            await bundle.adapters.memory.ingest_events(bundle.slug, events)
        return {"facts": len(events), "sources": sources}


@activity.defn(name="ingest_alert")
async def ingest_alert(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        event = payload.get("event", {})
        labels = event.get("payload", {}).get("labels", {})
        await bundle.adapters.memory.write_fact(
            bundle.slug,
            Fact(
                kind="incident",
                subject=f"incident:{labels.get('service', bundle.slug)}:{utcnow():%Y-%m-%d}",
                content=f"Alerte {labels.get('alertname', '?')} ({labels.get('severity', '?')}).",
                external_id=event.get("delivery_id"),
                provenance=Provenance(source="alertmanager", ref=event.get("delivery_id")),
            ),
        )
        return {"ingested": True}


@activity.defn(name="write_run_lesson")
async def write_run_lesson(payload: dict[str, Any]) -> dict[str, Any]:
    """Écriture gouvernée : l'orchestrateur écrit des faits déterministes, l'agent propose."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        if not bundle.engine.memory_enabled():
            return {"written": False}
        await bundle.adapters.memory.write_fact(
            bundle.slug,
            Fact(
                kind="run_lesson",
                subject=payload["subject"],
                content=payload["content"],
                external_id=payload.get("external_id"),
                provenance=Provenance(source="orchestrator", run_id=payload.get("run_id")),
            ),
        )
        return {"written": True}


@activity.defn(name="accept_pending_facts")
async def accept_pending_facts(payload: dict[str, Any]) -> dict[str, Any]:
    """Règle d'auto-acceptation : un fait proposé par ≥ N runs devient actif sans humain."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        adapter = bundle.adapters.memory
        pending = getattr(adapter, "pending", {}).get(bundle.slug, [])
        threshold = bundle.policy.memory.auto_accept_after_runs
        counts: dict[str, int] = {}
        for memory in pending:
            counts[memory.subject] = counts.get(memory.subject, 0) + 1
        accepted = 0
        for memory in list(pending):
            if counts.get(memory.subject, 0) >= threshold and hasattr(adapter, "accept_pending"):
                await adapter.accept_pending(bundle.slug, memory.id)
                accepted += 1
        return {"accepted": accepted, "threshold": threshold}


@activity.defn(name="memory_ab_report")
async def memory_ab_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Rapport A/B hebdomadaire : la mémoire paie-t-elle ? (docs/plan/04, décision 4)

    Le calcul vit dans `choregos_api.services` — c'est l'API qui possède le modèle de
    données, et le même rapport est servi par `GET /orgs/{org}/memory/ab-report`. Ici, on
    l'exécute et, si on le demande, on le poste là où les humains le liront.
    """
    from choregos_api.services import memory_ab_comparison

    async with db() as session:
        report = await memory_ab_comparison(session, str(payload["org"]), int(payload.get("weeks", 4)))
    projects = report["groups"]["with_memory"]["projects"] + report["groups"]["without_memory"]["projects"]
    if payload.get("notify") and projects:
        async with db() as session:
            bundle = await project_bundle(session, projects[0])
            await bundle.adapters.notify.send(
                bundle.config.notify.slack_channel or "#choregos",
                Message(
                    title=f"Mémoire : rapport A/B sur {report['weeks']} semaines — {report['verdict']}",
                    body=str(report["detail"]),
                    severity="warning" if report["verdict"] == "la mémoire ne paie pas" else "info",
                ),
            )
    return report
