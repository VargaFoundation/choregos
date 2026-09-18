"""Activités du release train : fenêtres, promotion GitOps, soak, canary, rollback, notes."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any

from choregos_api.db.models import Deployment, Finding, Project, Release, WorkItem
from choregos_api.services import persist_event
from choregos_contracts import EventType
from choregos_core import Change, Message, PrRef, utcnow
from sqlalchemy import func, select
from temporalio import activity

from ..config import get_settings
from .base import db, project_bundle

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


async def _observe(seconds: float) -> None:
    """Attente d'observation (soak, palier de canary).

    Avec les adaptateurs simulés il n'y a rien à observer : l'attente est inutile et
    ralentirait les tests et la démo. En réel, elle est plafonnée et jalonnée de heartbeats.
    """
    if get_settings().fakes or seconds <= 0:
        return
    await asyncio.sleep(min(seconds, 30.0))


@activity.defn(name="load_train_config")
async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        env_policy = bundle.policy.release_train.get(payload["env"])
        if env_policy is None:
            return {"mode": "auto_sync"}
        config = env_policy.model_dump(mode="json", exclude_none=True)
        express = env_policy.express_lane
        if express is not None:
            config["express_soak_minutes"] = express.soak_minutes
        config["project_id"] = bundle.project.id
        config["apps"] = list(bundle.config.gitops.apps) if bundle.config.gitops else [bundle.slug]
        return config


def _parse_windows(windows: list[str]) -> list[tuple[set[int], int, int]]:
    """`Mon-Thu 09:00-18:00` → (jours, minute de début, minute de fin)."""
    parsed: list[tuple[set[int], int, int]] = []
    for window in windows:
        try:
            days_part, hours_part = window.split(" ", 1)
            start_h, end_h = hours_part.split("-", 1)
            days: set[int] = set()
            for chunk in days_part.split(","):
                if "-" in chunk:
                    first, last = chunk.split("-", 1)
                    start, end = WEEKDAYS[first.strip().lower()[:3]], WEEKDAYS[last.strip().lower()[:3]]
                    days.update(range(start, end + 1))
                else:
                    days.add(WEEKDAYS[chunk.strip().lower()[:3]])
            parsed.append((days, _minutes(start_h), _minutes(end_h)))
        except (ValueError, KeyError):
            continue
    return parsed


def _minutes(value: str) -> int:
    hours, _, minutes = value.strip().partition(":")
    return int(hours) * 60 + int(minutes or 0)


def _cron_due(schedule: str, now: datetime) -> bool:
    """Cron à 5 champs, résolution minute : suffisant pour une cadence de train."""
    try:
        minute, hour, dom, month, dow = schedule.split()
    except ValueError:
        return False

    def matches(field: str, value: int, *, names: dict[str, int] | None = None) -> bool:
        if field == "*":
            return True
        for part in field.split(","):
            if part.startswith("*/"):
                if value % int(part[2:]) == 0:
                    return True
            elif "-" in part:
                start, end = part.split("-", 1)
                lo = names[start.lower()[:3]] if names and not start.isdigit() else int(start)
                hi = names[end.lower()[:3]] if names and not end.isdigit() else int(end)
                if lo <= value <= hi:
                    return True
            else:
                target = names[part.lower()[:3]] if names and not part.isdigit() else int(part)
                if value == target:
                    return True
        return False

    dow_value = (now.weekday() + 1) % 7  # cron : 0 = dimanche
    return (
        matches(minute, now.minute)
        and matches(hour, now.hour)
        and matches(dom, now.day)
        and matches(month, now.month)
        and matches(
            dow, dow_value, names={"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
        )
    )


@activity.defn(name="check_window")
async def check_window(payload: dict[str, Any]) -> dict[str, Any]:
    """La fenêtre est-elle ouverte, et le cron est-il dû ?"""
    config = payload["config"]
    now = datetime.now(UTC)
    windows = _parse_windows(list(config.get("windows", [])))
    open_now = True
    if windows:
        minute_of_day = now.hour * 60 + now.minute
        open_now = any(
            now.weekday() in days and start <= minute_of_day <= end for days, start, end in windows
        )
    due = bool(config.get("schedule")) and _cron_due(str(config["schedule"]), now)
    next_departure = (now + timedelta(minutes=30)).isoformat()
    return {"open": open_now, "due": due, "wait_seconds": 60, "next_departure": next_departure}


@activity.defn(name="create_release")
async def create_release(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        existing = (
            await session.execute(
                select(Release).where(
                    Release.project_id == bundle.project.id,
                    Release.env == payload["env"],
                    Release.batch_no == payload["batch_no"],
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return {"release_id": existing.id, "reused": True}
        row = Release(
            project_id=bundle.project.id,
            env=payload["env"],
            batch_no=payload["batch_no"],
            status="departing",
            items=payload["items"],
            started_at=utcnow(),
            notes="voie express (hotfix)" if payload.get("express") else None,
        )
        session.add(row)
        await session.flush()
        await persist_event(
            session,
            EventType.RELEASE_DEPARTED,
            project_id=bundle.project.id,
            project_slug=bundle.slug,
            subject=row.id,
            release_id=row.id,
            env=payload["env"],
            items=len(payload["items"]),
        )
        return {"release_id": row.id, "reused": False}


@activity.defn(name="promote")
async def promote(payload: dict[str, Any]) -> dict[str, Any]:
    """Promotion GitOps : une PR sur le dépôt d'environnements, jamais un `kubectl apply`."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        release = await session.get(Release, payload["release_id"])
        if release is None:
            return {"ok": False, "reason": "release inconnue"}
        apps = list(bundle.config.gitops.apps) if bundle.config.gitops else [bundle.slug]
        tag = f"R-{utcnow():%Y.%m.%d}-{release.batch_no}"
        changes = [Change(app=app, tag=tag) for app in apps]
        ref = await bundle.adapters.cd.promote(payload["env"], changes, tag)
        release.status = "staging"
        release.promotion_url = ref.url
        session.add(
            Deployment(
                release_id=release.id,
                env=payload["env"],
                revision=tag,
                cd_ref=ref.ref,
                status="promoting",
                started_at=utcnow(),
            )
        )
        await persist_event(
            session,
            EventType.RELEASE_STAGED,
            project_id=bundle.project.id,
            project_slug=bundle.slug,
            subject=release.id,
            release_id=release.id,
            env=payload["env"],
            promotion_url=ref.url,
        )
        return {"ok": True, "promotion_url": ref.url, "revision": tag}


async def _close_deployment(session: Any, release_id: str, status: str) -> None:
    """Clôt la ligne de déploiement ouverte par `promote`.

    Sans cette clôture, un déploiement resterait éternellement « promoting » : les
    métriques de livraison ne sauraient pas distinguer une mise en production réussie
    d'une qui a été annulée.
    """
    row = (
        (
            await session.execute(
                select(Deployment)
                .where(Deployment.release_id == release_id, Deployment.ended_at.is_(None))
                .order_by(Deployment.started_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if row is not None:
        row.status = status
        row.ended_at = utcnow()


@activity.defn(name="run_smoke")
async def run_smoke(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        apps = list(bundle.config.gitops.apps) if bundle.config.gitops else [bundle.slug]
        for app in apps:
            health = await bundle.adapters.cd.health(app)
            if health.status in {"Degraded", "Missing"}:
                return {"ok": False, "reason": f"{app} : {health.status} {health.message}"}
        return {"ok": True}


@activity.defn(name="soak")
async def soak(payload: dict[str, Any]) -> dict[str, Any]:
    """Période d'observation : on regarde la santé, on ne déploie rien de plus."""
    minutes = int(payload.get("minutes", 10))
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        apps = list(bundle.config.gitops.apps) if bundle.config.gitops else [bundle.slug]
    checks = max(1, min(minutes, 6))
    interval = (minutes * 60) / checks if minutes else 0
    for _ in range(checks):
        await _observe(interval)
        with contextlib.suppress(RuntimeError):
            activity.heartbeat({"phase": "soak"})
        async with db() as session:
            bundle = await project_bundle(session, payload["project_slug"])
            for app in apps:
                health = await bundle.adapters.cd.health(app)
                if health.status == "Degraded":
                    return {"ok": False, "reason": f"{app} dégradé pendant le soak"}
    return {"ok": True}


@activity.defn(name="request_approval")
async def request_approval(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        release = await session.get(Release, payload["release_id"])
        if release is None:
            return {"requested": False}
        release.status = "awaiting_approval"
        await bundle.adapters.notify.send(
            bundle.config.notify.slack_channel or "#choregos",
            Message(
                title=f"Approbation demandée — {bundle.slug} → {payload['env']}",
                body=f"Lot {release.batch_no} : {len(release.items)} ticket(s).",
                url=f"{settings.public_url}/p/{bundle.slug}/trains",
                severity="warning",
            ),
        )
        await persist_event(
            session,
            EventType.RELEASE_APPROVAL_REQUESTED,
            project_id=bundle.project.id,
            project_slug=bundle.slug,
            subject=release.id,
            release_id=release.id,
            group=payload.get("group"),
        )
        return {"requested": True}


@activity.defn(name="promote_canary_step")
async def promote_canary_step(payload: dict[str, Any]) -> dict[str, Any]:
    """Un palier de canary : on augmente le poids, on regarde l'analyse, on abandonne si elle échoue."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        apps = list(bundle.config.gitops.apps) if bundle.config.gitops else [bundle.slug]
        release = await session.get(Release, payload["release_id"])
        if release is not None:
            release.status = "promoting"
    await _observe(int(payload.get("minutes", 0)) * 60)
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        for app in apps:
            state = await bundle.adapters.cd.rollout_status(app)
            if state.phase in {"Degraded", "Aborted"}:
                await bundle.adapters.cd.abort_rollout(app)
                return {"ok": False, "reason": f"analyse KO sur {app} : {state.message}"}
    return {"ok": True, "weight": payload.get("weight")}


@activity.defn(name="verify_prod")
async def verify_prod(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        apps = list(bundle.config.gitops.apps) if bundle.config.gitops else [bundle.slug]
        for app in apps:
            health = await bundle.adapters.cd.health(app)
            if health.status not in {"Healthy", "Progressing"}:
                return {"ok": False, "reason": f"{app} : {health.status}"}
        release = await session.get(Release, payload["release_id"])
        if release is not None:
            release.verdict = {"go": True, "checked_apps": apps}
        return {"ok": True}


@activity.defn(name="finish_release")
async def finish_release(payload: dict[str, Any]) -> dict[str, Any]:
    """Clôture : notes de version, tickets marqués déployés, événement, notification."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        release = await session.get(Release, payload["release_id"])
        if release is None:
            return {"ok": False}
        release.status = "done"
        release.ended_at = utcnow()
        release.approved_by = payload.get("approved_by") or release.approved_by
        await _close_deployment(session, release.id, "succeeded")
        notes = _release_notes(release)
        release.notes = notes
        for item in release.items:
            row = (
                await session.execute(
                    select(WorkItem).where(
                        WorkItem.project_id == bundle.project.id,
                        WorkItem.tracker_key == item.get("work_item_key"),
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                documents = dict(row.documents or {})
                documents["deployed_release"] = release.id
                row.documents = documents
        await persist_event(
            session,
            EventType.RELEASE_VERIFIED,
            project_id=bundle.project.id,
            project_slug=bundle.slug,
            subject=release.id,
            release_id=release.id,
            env=release.env,
            items=len(release.items),
        )
        await bundle.adapters.notify.send(
            bundle.config.notify.slack_channel or "#choregos",
            Message(
                title=f"Déployé — {bundle.slug} → {release.env} (lot {release.batch_no})",
                body=notes,
                severity="success",
            ),
        )
        return {"ok": True, "notes": notes}


def _release_notes(release: Release) -> str:
    lines = [f"### Release R-{release.batch_no} — {release.env}", ""]
    for item in release.items:
        title = item.get("title") or item.get("work_item_key")
        lines.append(f"- {item.get('work_item_key')} — {title}")
    return "\n".join(lines)


@activity.defn(name="rollback")
async def rollback(payload: dict[str, Any]) -> dict[str, Any]:
    """Rollback : abandon du rollout, release marquée, incident enregistré, alerte envoyée."""
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        release = await session.get(Release, payload["release_id"])
        apps = list(bundle.config.gitops.apps) if bundle.config.gitops else [bundle.slug]
        for app in apps:
            await bundle.adapters.cd.abort_rollout(app)
        if release is not None:
            release.status = "rolled_back"
            release.ended_at = utcnow()
            release.verdict = {"go": False, "reason": payload.get("reason", "")}
            await _close_deployment(session, release.id, "rolled_back")
            session.add(
                Finding(
                    project_id=bundle.project.id,
                    title=f"Rollback {bundle.slug} → {release.env} (lot {release.batch_no})",
                    type="bug",
                    severity="critical",
                    evidence=str(payload.get("reason", "")),
                    status="pending",
                )
            )
            await persist_event(
                session,
                EventType.RELEASE_ROLLED_BACK,
                project_id=bundle.project.id,
                project_slug=bundle.slug,
                subject=release.id,
                release_id=release.id,
                env=release.env,
                reason=payload.get("reason"),
            )
        if bundle.engine.memory_enabled():
            from choregos_core import Fact, Provenance

            await bundle.adapters.memory.write_fact(
                bundle.slug,
                Fact(
                    kind="incident",
                    subject=f"incident:{bundle.slug}:{utcnow():%Y-%m-%d}",
                    content=f"Rollback sur {release.env if release else '?'} : {payload.get('reason', '')}",
                    provenance=Provenance(source="cd", ref=payload.get("release_id")),
                ),
            )
        await bundle.adapters.notify.send(
            bundle.config.notify.slack_channel or "#choregos",
            Message(
                title=f"Rollback — {bundle.slug} → {payload['env']}",
                body=str(payload.get("reason", "")),
                severity="error",
            ),
        )
        return {"ok": True}


@activity.defn(name="mark_release")
async def mark_release(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        release = await session.get(Release, payload["release_id"])
        if release is None:
            return {"ok": False}
        release.status = payload["status"]
        if payload.get("reason"):
            release.notes = payload["reason"]
        return {"ok": True}


@activity.defn(name="train_stats")
async def train_stats(payload: dict[str, Any]) -> dict[str, Any]:
    async with db() as session:
        project = (
            await session.execute(select(Project).where(Project.slug == payload["project_slug"]))
        ).scalar_one_or_none()
        if project is None:
            return {}
        count = (
            await session.execute(
                select(func.count()).select_from(Release).where(Release.project_id == project.id)
            )
        ).scalar_one()
        return {"releases": int(count)}


@activity.defn(name="apply_terraform")
async def apply_terraform(payload: dict[str, Any]) -> dict[str, Any]:
    """Déclenche l'`apply` Atlantis des PR d'infra du lot, après approbation (docs/plan/05).

    Choregos ne détient aucun credential cloud : il commente `atlantis apply` sur la PR
    d'infra et attend le verdict du check `atlantis/apply`. C'est Atlantis qui tient le
    lock et les credentials ; le train se contente de choisir le moment — pendant un
    départ, après l'approbation, jamais entre deux.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_slug"])
        spec = getattr(bundle.engine.train(payload["env"]), "terraform", None)
        if spec is None or str(getattr(spec, "via", "none")) != "atlantis":
            return {"applied": [], "skipped": "atlantis non configuré pour cet environnement"}
        release = await session.get(Release, payload["release_id"])
        if release is None:
            return {"applied": [], "skipped": "release inconnue"}
        prs = [
            (item.get("work_item_key", ""), str(item["infra_pr_url"]))
            for item in release.items
            if item.get("infra_pr_url")
        ]
        scm = bundle.adapters.scm
        slug = bundle.slug
        channel = bundle.config.notify.slack_channel or "#choregos"
        notify = bundle.adapters.notify

    if not prs:
        return {"applied": [], "skipped": "aucune PR d'infra dans ce lot"}

    applied: list[str] = []
    for key, url in prs:
        ref = _pr_ref(url)
        if ref is None:
            return {"applied": applied, "ok": False, "reason": f"URL de PR illisible : {url}"}
        await scm.comment_pr(ref, "atlantis apply")
        verdict = await _await_atlantis(scm, ref, int(payload.get("timeout_minutes", 30)))
        if verdict != "success":
            await notify.send(
                channel,
                Message(
                    title=f"Terraform : apply en échec — {slug} → {payload['env']}",
                    body=f"{key} · {url} : {verdict}",
                    severity="error",
                ),
            )
            return {"applied": applied, "ok": False, "reason": f"apply {verdict} sur {url}"}
        applied.append(url)
    return {"applied": applied, "ok": True}


def _pr_ref(url: str) -> PrRef | None:
    """`https://github.com/org/repo/pull/42` → `PrRef(repo="org/repo", number=42)`."""
    parts = [segment for segment in url.split("/") if segment]
    if len(parts) < 4 or not parts[-1].isdigit():
        return None
    return PrRef(repo=f"{parts[-4]}/{parts[-3]}", number=int(parts[-1]), url=url)


POLL_SECONDS = 15


async def _await_atlantis(scm: Any, ref: PrRef, timeout_minutes: int) -> str:
    """Attend le check `atlantis/apply`. Rend sa conclusion, ou `timeout`.

    Le nombre de sondages est borné, pas seulement la durée : avec les adaptateurs
    simulés, l'attente ne coûte rien et une borne temporelle seule tournerait à vide.
    """
    polls = max(1, int(timeout_minutes * 60 / POLL_SECONDS))
    for _ in range(polls):
        state = await scm.get_pr(ref)
        check = next((c for c in state.checks if c.name == "atlantis/apply"), None)
        if check is not None and check.status == "completed":
            return str(check.conclusion or "unknown")
        with contextlib.suppress(RuntimeError):
            activity.heartbeat({"pr": ref.number})
        await _observe(POLL_SECONDS)
    return "timeout"
