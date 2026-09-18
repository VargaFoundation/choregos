"""Activités tracker : miroir d'état, commentaire de suivi, champs du board, demandes humaines."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from choregos_api.db.models import Finding, HumanRequest, Run, WorkItem
from choregos_api.services import persist_event
from choregos_contracts import EventType
from choregos_core import Message, MessageAction, TrackerStateMapping, elapsed_seconds, utcnow
from sqlalchemy import select
from temporalio import activity

from ..config import get_settings
from ..markdown import STATUS_MARKER, StageLine, StatusComment, render_human_request
from .base import db, load_work_item, project_bundle


@activity.defn(name="mirror_state")
async def mirror_state(payload: dict[str, Any]) -> dict[str, Any]:
    """Reflète l'état du DSL dans le tracker : colonne du board et label scopé."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        state_name = payload["state"]
        state = bundle.workflow.states.get(state_name)
        previous = item.state
        item.state = state_name
        if state is not None and state.terminal and item.closed_at is None:
            item.closed_at = utcnow()
        mapping = TrackerStateMapping(
            status=(state.tracker.status if state and state.tracker else None),
            label=(state.tracker.label if state and state.tracker else f"choregos:{state_name}"),
        )
        await bundle.adapters.tracker.set_state(item.tracker_key, mapping)
        if previous != state_name:
            await persist_event(
                session,
                EventType.WORKITEM_STATE_CHANGED,
                project_id=bundle.project.id,
                work_item_id=item.id,
                project_slug=bundle.slug,
                subject=item.tracker_key,
                **{"from": previous, "to": state_name, "reason": payload.get("reason", "")},
            )
        return {"state": state_name, "display": state.display if state else state_name}


@activity.defn(name="update_status_comment")
async def update_status_comment(payload: dict[str, Any]) -> dict[str, str]:
    """Réécrit l'unique commentaire de suivi et les champs structurés du board (§4.3)."""
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        runs = (
            (await session.execute(select(Run).where(Run.work_item_id == item.id).order_by(Run.created_at)))
            .scalars()
            .all()
        )
        requests = (
            (
                await session.execute(
                    select(HumanRequest)
                    .where(HumanRequest.work_item_id == item.id)
                    .order_by(HumanRequest.requested_at)
                )
            )
            .scalars()
            .all()
        )
        findings = (
            (await session.execute(select(Finding).where(Finding.origin_work_item_id == item.id)))
            .scalars()
            .all()
        )
        finding_labels: list[str] = []
        for finding in findings:
            created = (
                await session.get(WorkItem, finding.created_work_item_id)
                if finding.created_work_item_id
                else None
            )
            reference = created.tracker_key if created else finding.id[:8]
            finding_labels.append(f"{reference} ({finding.type}, {finding.severity})")

        lines: list[StageLine] = []
        events: list[tuple[Any, str]] = [(run, "run") for run in runs] + [(req, "human") for req in requests]
        events.sort(key=lambda pair: getattr(pair[0], "created_at", None) or pair[0].requested_at)
        for index, (entry, kind) in enumerate(events, start=1):
            if kind == "run":
                tokens = entry.tokens or {}
                state = (
                    bundle.workflow.states.get(entry.stage_input.get("transition", {}).get("to", ""))
                    if entry.stage_input
                    else None
                )
                duration = elapsed_seconds(entry.started_at, entry.ended_at)
                outcome = (entry.result or {}).get("summary", entry.status)
                lines.append(
                    StageLine(
                        index=index,
                        label=state.display if state else entry.stage_role,
                        actor=f"agent {entry.stage_role}",
                        backend=entry.backend,
                        model=(entry.model or "").split("/")[-1],
                        tokens_in=int(tokens.get("tokens_in", 0)),
                        tokens_out=int(tokens.get("tokens_out", 0)),
                        tokens_cached=int(tokens.get("tokens_cached", 0)),
                        cost_eur=float(tokens.get("cost_eur", 0.0)),
                        duration_s=duration,
                        outcome=str(outcome)[:120],
                    )
                )
            else:
                decided = entry.decided_at
                duration = elapsed_seconds(entry.requested_at, decided)
                decision = (entry.decision or {}).get("approved")
                outcome = "en attente" if decided is None else ("approuvée" if decision else "renvoyée")
                lines.append(
                    StageLine(
                        index=index,
                        label={
                            "approval": "Validation",
                            "question": "Question",
                            "scope_change": "Périmètre",
                        }.get(entry.kind, entry.kind),
                        actor=entry.decided_by or "humain",
                        duration_s=duration,
                        outcome=outcome,
                    )
                )

        totals = item.totals or {}
        engine = bundle.engine
        budget_usd = engine.budget_ticket(item.size)
        state = bundle.workflow.states.get(item.state)
        estimate = payload.get("estimate_eur")
        comment = StatusComment(
            workflow=bundle.workflow.metadata.name,
            workflow_version=bundle.workflow.metadata.version,
            size=item.size,
            risk=item.risk,
            state_display=state.display if state else item.state,
            budget_eur=round(budget_usd * settings.fx_usd_eur, 2),
            spent_eur=float(totals.get("cost_eur", 0.0)),
            pr_url=item.pr_url,
            run_url=f"{settings.public_url}/p/{bundle.slug}/items/{item.id}",
            estimate_eur=estimate,
            over_estimate=bool(payload.get("over_estimate")),
            findings=finding_labels,
            memory_note=payload.get("memory_note"),
            lines=lines,
        )
        markdown = comment.render()
        await bundle.adapters.tracker.upsert_status_comment(item.tracker_key, markdown, STATUS_MARKER)
        await bundle.adapters.tracker.set_fields(
            item.tracker_key,
            {
                "Coût (€)": round(float(totals.get("cost_eur", 0.0)), 2),
                "Taille": item.size or "",
                "Risque": item.risk or "",
                "Run": f"{settings.public_url}/p/{bundle.slug}/items/{item.id}",
            },
        )
        return {"markdown": markdown}


@activity.defn(name="create_human_request")
async def create_human_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Crée la demande humaine, la poste sur le ticket et notifie — idempotente par transition."""
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        existing = (
            await session.execute(
                select(HumanRequest).where(
                    HumanRequest.work_item_id == item.id,
                    HumanRequest.transition_id == payload.get("transition_id"),
                    HumanRequest.kind == payload["kind"],
                    HumanRequest.decided_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"request_id": existing.id, "reused": True}

        sla_hours = int(payload.get("sla_hours") or 24)
        row = HumanRequest(
            work_item_id=item.id,
            project_id=bundle.project.id,
            transition_id=payload.get("transition_id"),
            kind=payload["kind"],
            payload=payload.get("payload", {}),
            requested_at=utcnow(),
            due_at=utcnow() + timedelta(hours=sla_hours),
        )
        session.add(row)
        await session.flush()

        markdown = render_human_request(
            payload["kind"], payload.get("payload", {}), settings.public_url, item.tracker_key
        )
        await bundle.adapters.tracker.comment(item.tracker_key, markdown)
        channel = bundle.config.notify.slack_channel or "#choregos"
        await bundle.adapters.notify.send(
            channel,
            Message(
                title=f"Choregos — {payload['kind']} sur {item.tracker_key}",
                body=item.title,
                url=f"{settings.public_url}/p/{bundle.slug}/items/{item.id}",
                severity="warning",
                actions=[
                    MessageAction(id="approve", label="Approuver", style="primary", value=row.id),
                    MessageAction(id="reject", label="Renvoyer", style="danger", value=row.id),
                ],
            ),
        )
        await persist_event(
            session,
            EventType.WORKITEM_HUMAN_REQUESTED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=item.tracker_key,
            kind=payload["kind"],
            request_id=row.id,
        )
        return {"request_id": row.id, "reused": False}


@activity.defn(name="close_human_request")
async def close_human_request(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        row = await session.get(HumanRequest, payload["request_id"]) if payload.get("request_id") else None
        if row is None:
            return {"closed": False}
        if row.decided_at is None:
            row.decided_at = utcnow()
            row.decided_by = payload.get("decided_by", "système")
            row.decision = payload.get("decision", {})
        return {"closed": True}


@activity.defn(name="notify")
async def notify(payload: dict[str, Any]) -> None:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        channel = payload.get("channel") or bundle.config.notify.slack_channel or "#choregos"
        await bundle.adapters.notify.send(channel, Message.model_validate(payload["message"]))


@activity.defn(name="close_out")
async def close_out(payload: dict[str, Any]) -> dict[str, Any]:
    """Fin de ticket : état terminal reflété, commentaire final, leçon écrite en mémoire."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        item = await load_work_item(session, payload["work_item_id"])
        if item.closed_at is None:
            item.closed_at = utcnow()
        await persist_event(
            session,
            EventType.WORKITEM_CLOSED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=item.tracker_key,
            state=item.state,
            cost_usd=float((item.totals or {}).get("cost_usd", 0.0)),
        )
        if bundle.engine.memory_enabled():
            from choregos_core import Fact, Provenance

            runs = (await session.execute(select(Run).where(Run.work_item_id == item.id))).scalars().all()
            attempts = max((run.attempt for run in runs), default=1)
            lesson = (
                f"Ticket {item.tracker_key} ({item.size or '?'}, risque {item.risk or '?'}) terminé en "
                f"{len(runs)} runs, {attempts} tentative(s) max, "
                f"{float((item.totals or {}).get('cost_usd', 0.0)):.2f} USD."
            )
            await bundle.adapters.memory.write_fact(
                bundle.slug,
                Fact(
                    kind="ticket_summary",
                    subject=f"ticket_summary:{item.tracker_key}",
                    content=lesson,
                    external_id=f"workitem:{item.tracker_key}",
                    provenance=Provenance(source="orchestrator", work_item_key=item.tracker_key),
                ),
            )
        return {"closed": True, "state": item.state}
