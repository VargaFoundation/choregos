# SPDX-License-Identifier: Apache-2.0
"""Memory activities: idempotent ingestion, context pack, governed writing."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_api.db.models import Finding, Release, Run, WorkItem
from choregos_core import Fact, Message, Provenance, memory_namespace, utcnow
from choregos_core.memory_scope import parse_namespace
from choregos_core.restricted import is_marked
from sqlalchemy import select
from temporalio import activity

from .base import db, notification_target, org_memory, project_bundle, send_courtesy


def _write_target(project: str, run: Any) -> tuple[str | None, bool]:
    """Where a fact born of a run goes, and whether it is only a proposal (S32-02, S40-11).

    A run prepared with `StageInput.memories` writes to its one `write` binding — as a proposal when
    that memory is global — or nowhere when it has none. A run without them (started before S40-11,
    or a playbook) follows the memory scope its `StageInput` carried, so replays and runs in flight
    behave as before.
    """
    if run is None:
        return project, False
    stage_input = run.stage_input or {}
    memories = stage_input.get("memories")
    if memories:
        target = next((m for m in memories if m.get("mode") == "write"), None)
        if target is None:
            return None, False
        return str(target["namespace"]), target.get("scope") == "global"
    scope = str(stage_input.get("memory_scope") or "project")
    return memory_namespace(project, scope, run.agent_slug), False


def _namespace_of(project: str, run: Any) -> str | None:
    """Where a fact born of a run goes: its write target's namespace (see `_write_target`)."""
    return _write_target(project, run)[0]


def _adapter_for(bundle: Any, org_adapter: Any, namespace: str) -> Any:
    """The adapter that serves a namespace: the organisation's for a global memory, else the project's."""
    return org_adapter if parse_namespace(namespace).kind == "global_store" else bundle.adapters.memory


async def _propose_events(adapter: Any, namespace: str, events: list[dict[str, Any]]) -> None:
    """Hand events to a memory as proposals, pending a human decision (a global write target)."""
    for event in events:
        await adapter.propose_fact(
            namespace,
            Fact(
                kind=event.get("kind", "other"),
                subject=str(event.get("subject", "")),
                content=str(event.get("content", "")),
                external_id=event.get("external_id"),
            ),
            Provenance(
                source=str(event.get("source", "ingest")),
                ref=event.get("external_id"),
                run_id=event.get("_run_id"),
            ),
        )


async def _runs_by_id(session: Any, ids: list[str]) -> dict[str, Any]:
    if not ids:
        return {}
    rows = await session.execute(select(Run).where(Run.id.in_(ids)))
    return {run.id: run for run in rows.scalars()}


async def _marked_items(session: Any, ids: set[str | None]) -> set[str]:
    """Which of these work items are marked by a restricted knowledge base (ADR 0114)."""
    wanted = [i for i in ids if i]
    if not wanted:
        return set()
    rows = await session.execute(select(WorkItem).where(WorkItem.id.in_(wanted)))
    return {item.id for item in rows.scalars() if is_marked(item)}


def _failed_run_lesson(project: str, run: Any) -> dict[str, Any]:
    """The lesson of a failed run, as an ingestion event: one per run (`run:<id>`), wherever it is
    written from — the run's own outcome (S51-02) or the `run_lessons` source — so it is kept once."""
    reason = (run.result or {}).get("reason") or "failed"
    namespace, propose = _write_target(project, run)
    return {
        "_namespace": namespace,
        "_propose": propose,
        "_run_id": run.id,
        "external_id": f"run:{run.id}",
        "kind": "run_lesson",
        "subject": f"run_lesson:{project}:{run.stage_role}",
        "content": f"The {run.stage_role} step failed ({reason}) — attempt {run.attempt}.",
        "source": "orchestrator",
    }


async def keep_the_lesson_of_a_failed_run(run_id: str) -> dict[str, Any]:
    """Write a failed run's lesson as soon as its outcome is recorded (S51-02), not only when the
    memory loop next ingests `run_lessons`: same fact, same `run:<id>`, so neither path doubles it.

    Respects what the ingestion respects: the project's policy (`memory.enabled`), the run's write
    target (none, a project memory, or a global one as a proposal), and a work item marked by a
    restricted knowledge base (ADR 0114 §3). Called after the outcome is committed.
    """
    async with db() as session:
        run = await session.get(Run, run_id)
        if run is None or run.status != "failed":
            return {"written": False}
        bundle = await project_bundle(session, run.project_id)
        if not bundle.engine.memory_enabled():
            return {"written": False, "skipped": "memory disabled"}
        if await _marked_items(session, {run.work_item_id}):
            return {"written": False, "skipped": "restricted"}
        event = _failed_run_lesson(bundle.slug, run)
        namespace, propose = event.pop("_namespace"), bool(event.pop("_propose"))
        if namespace is None:
            return {"written": False, "skipped": "the agent keeps no memory"}
        org_adapter = await org_memory(session, bundle) if propose else None
        adapter = _adapter_for(bundle, org_adapter, namespace)
        if propose:
            await _propose_events(adapter, namespace, [event])
            return {"written": False, "proposed": True}
        event.pop("_run_id", None)
        await adapter.ingest_events(namespace, [event])
        return {"written": True}


@activity.defn(name="ingest_sources")
async def ingest_sources(payload: dict[str, Any]) -> dict[str, Any]:
    """Build facts from the platform state and push them as an idempotent upsert."""
    sources: list[str] = payload.get("sources", [])
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        if not bundle.engine.memory_enabled():
            return {"facts": 0, "skipped": "memory disabled"}
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
            # No shared memory from a marked work item (ADR 0114 §3): later runs would read it unchecked.
            for item in [i for i in items if not is_marked(i)]:
                cost = float((item.totals or {}).get("cost_usd", 0.0))
                events.append(
                    {
                        "external_id": f"workitem:{item.tracker_key}",
                        "kind": "ticket_summary",
                        "subject": f"ticket_summary:{item.tracker_key}",
                        "content": f"{item.title} — final state {item.state}, {cost:.2f} USD.",
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
            marked = await _marked_items(session, {run.work_item_id for run in runs})
            events += [_failed_run_lesson(bundle.slug, r) for r in runs if r.work_item_id not in marked]

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
            origines = await _runs_by_id(session, [f.origin_run_id for f in findings if f.origin_run_id])
            marked = await _marked_items(session, {f.origin_work_item_id for f in findings})
            for finding in [f for f in findings if f.origin_work_item_id not in marked]:
                namespace, propose = _write_target(bundle.slug, origines.get(finding.origin_run_id or ""))
                events.append(
                    {
                        "_namespace": namespace,
                        "_propose": propose,
                        "_run_id": finding.origin_run_id,
                        "external_id": f"finding:{finding.id}",
                        "kind": "finding",
                        "subject": f"finding:{bundle.slug}:{finding.id}",
                        "content": f"{finding.title} — {finding.evidence}",
                        "source": "agent",
                    }
                )

        # Each fact goes where its run's write target is (S32-02, S40-11): the project, the agent's own,
        # a named memory, a global one (as proposals), or nowhere for a run that writes nothing.
        grouped: dict[tuple[str, bool], list[dict[str, Any]]] = {}
        for event in events:
            namespace = event.pop("_namespace", bundle.slug)
            propose = bool(event.pop("_propose", False))
            if namespace is not None:
                grouped.setdefault((namespace, propose), []).append(event)
        org_adapter = None
        for (namespace, propose), batch in grouped.items():
            if parse_namespace(namespace).kind == "global_store" and org_adapter is None:
                org_adapter = await org_memory(session, bundle)
            adapter = _adapter_for(bundle, org_adapter, namespace)
            if propose:
                await _propose_events(adapter, namespace, batch)
            else:
                await adapter.ingest_events(namespace, batch)
        return {"facts": len(events), "sources": sources}


@activity.defn(name="ingest_alert")
async def ingest_alert(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        if not bundle.engine.memory_enabled():  # the policy is authoritative (S51-01, S51-02)
            return {"ingested": False, "skipped": "memory disabled"}
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
    """Governed writing: the orchestrator writes deterministic facts, the agent proposes."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        if not bundle.engine.memory_enabled():
            return {"written": False}
        run = await session.get(Run, payload["run_id"]) if payload.get("run_id") else None
        espace, propose = _write_target(bundle.slug, run)
        if espace is None:
            return {"written": False, "skipped": "the agent keeps no memory"}
        provenance = Provenance(source="orchestrator", run_id=payload.get("run_id"))
        fact = Fact(
            kind="run_lesson",
            subject=payload["subject"],
            content=payload["content"],
            external_id=payload.get("external_id"),
            provenance=provenance,
        )
        adapter = _adapter_for(bundle, await org_memory(session, bundle), espace)
        if propose:
            # A global memory only ever receives proposals: an administrator decides (ADR 0075).
            await adapter.propose_fact(espace, fact, provenance)
            return {"written": False, "proposed": True}
        await adapter.write_fact(espace, fact)
        return {"written": True}


async def _project_namespaces(session: Any, bundle: Any) -> list[str]:
    """The namespaces a project's pending facts can be in: its shared memory, the own memory of every
    agent that ran in it, and its named memories. Never a global memory: an administrator decides those."""
    from choregos_api.db.models import MemoryStore

    agents = (
        await session.execute(
            select(Run.agent_slug)
            .where(Run.project_id == bundle.project.id, Run.agent_slug.is_not(None))
            .distinct()
            .order_by(Run.agent_slug)
        )
    ).scalars()
    stores = (
        await session.execute(
            select(MemoryStore.slug).where(
                MemoryStore.org_id == bundle.project.org_id,
                MemoryStore.owner_key == str(bundle.project.id),
                MemoryStore.status == "active",
            )
        )
    ).scalars()
    return [
        bundle.slug,
        *(f"{bundle.slug}#agent:{agent}" for agent in agents),
        *(f"{bundle.slug}#memory:{slug}" for slug in stores),
    ]


@activity.defn(name="accept_pending_facts")
async def accept_pending_facts(payload: dict[str, Any]) -> dict[str, Any]:
    """Auto-accept rule: a fact proposed by at least N runs becomes active without a person.

    Applies to the project's memories only (S40-11): its shared memory, the own memory of its agents and
    its named memories. A global memory's proposals wait for an administrator.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        adapter = bundle.adapters.memory
        threshold = bundle.policy.memory.auto_accept_after_runs
        accepted = 0
        for namespace in await _project_namespaces(session, bundle):
            if hasattr(adapter, "list_pending"):
                pending = list(await adapter.list_pending(namespace))
            else:
                pending = list(getattr(adapter, "pending", {}).get(namespace, []))
            counts: dict[str, int] = {}
            for memory in pending:
                counts[memory.subject] = counts.get(memory.subject, 0) + 1
            for memory in pending:
                if counts.get(memory.subject, 0) >= threshold and hasattr(adapter, "accept_pending"):
                    await adapter.accept_pending(namespace, memory.id)
                    accepted += 1
        return {"accepted": accepted, "threshold": threshold}


@activity.defn(name="memory_ab_report")
async def memory_ab_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Weekly A/B report: does the memory pay? (docs/plan/04, decision 4)

    The computation lives in `choregos_api.services`: the API owns the data model, and the same
    report is served by `GET /orgs/{org}/memory/ab-report`. Here, it is run and, if asked, posted
    where humans will read it.
    """
    from choregos_api.services import memory_ab_comparison

    async with db() as session:
        report = await memory_ab_comparison(session, str(payload["org"]), int(payload.get("weeks", 4)))
    projects = report["groups"]["with_memory"]["projects"] + report["groups"]["without_memory"]["projects"]
    if payload.get("notify") and projects:
        async with db() as session:
            bundle = await project_bundle(session, projects[0])
            report["notified"] = await send_courtesy(
                bundle.adapters.notify,
                notification_target(bundle),
                Message(
                    title=f"Memory: A/B report over {report['weeks']} weeks — {report['verdict']}",
                    body=str(report["detail"]),
                    severity="warning" if report["verdict"] == "memory does not pay off" else "info",
                ),
            )
    return report
