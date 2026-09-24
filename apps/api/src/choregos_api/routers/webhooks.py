"""Webhooks entrants : vérification de signature, dédup, normalisation, réponse < 500 ms.

Le traitement lourd n'a pas lieu ici : on normalise en `InboundEvent`, on déduplique
par `delivery_id`, et on signale Temporal. Tout le reste est asynchrone.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from choregos_contracts import InboundEvent, InboundEventType
from choregos_core import utcnow
from fastapi import APIRouter, Header, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db.models import Project, WebhookDelivery, WorkItem
from ..deps import Db
from ..errors import unauthorized
from ..logging import get_logger
from ..schemas import WebhookAck
from ..security import body_digest, verify_github_signature, verify_shared_secret
from ..temporal import deliver_inbound, get_temporal, interpreter_id, train_id

router = APIRouter(tags=["webhooks"], prefix="/webhooks")
logger = get_logger("choregos.webhooks")

AGENT_READY_LABEL = "agent-ready"


async def _already_seen(session: Any, source: str, delivery_id: str, event_type: str, body: bytes) -> bool:
    """Dédup par (source, delivery_id) : un rejeu ne relance rien."""
    existing = (
        await session.execute(
            select(WebhookDelivery).where(
                WebhookDelivery.source == source, WebhookDelivery.delivery_id == delivery_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return True
    session.add(
        WebhookDelivery(
            source=source,
            delivery_id=delivery_id,
            event_type=event_type,
            ts=utcnow(),
            payload_digest=body_digest(body),
        )
    )
    try:
        await session.flush()
    except IntegrityError:  # course entre deux réplicas
        await session.rollback()
        return True
    return False


async def _project_for(session: Any, slug_or_repo: str | None) -> Project | None:
    """Le projet visé par un événement : par son dépôt d'abord, par son slug s'il est unique.

    Deux organisations peuvent avoir un projet `billing-api` ; prendre « le premier » routait
    l'événement d'un locataire vers l'autre. Le nom complet du dépôt (`owner/repo`) lève
    l'ambiguïté ; sans lui, un slug présent deux fois ne route rien — et le dit.
    """
    if not slug_or_repo:
        return None
    slug = slug_or_repo.rsplit("/", 1)[-1]
    rows = [
        r
        for r in (await session.execute(select(Project).where(Project.slug == slug))).scalars()
        if isinstance(r, Project)
    ]
    if len(rows) > 1 and "/" in slug_or_repo:
        rows = [
            r
            for r in rows
            if str((r.config or {}).get("repo", {}).get("url", ""))
            .removesuffix(".git")
            .endswith(slug_or_repo)
        ]
    if len(rows) != 1:
        if rows:
            logger.warning("événement ambigu entre plusieurs projets, non routé", slug=slug_or_repo)
        return None
    return rows[0]


async def _dispatch(session: Any, events: list[InboundEvent]) -> int:
    """Achemine chaque événement : signal au workflow du ticket, ou démarrage si `agent-ready`."""
    delivered = 0
    for event in events:
        project = await _project_for(session, event.project_slug)
        if project is None:
            logger.info("événement sans projet connu", event_type=str(event.type), slug=event.project_slug)
            continue
        key = event.work_item_key
        if key is None:
            continue
        item = (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == project.id, WorkItem.tracker_key == key)
            )
        ).scalar_one_or_none()

        should_start = event.type in {InboundEventType.ITEM_LABELED, InboundEventType.ITEM_CREATED} and (
            AGENT_READY_LABEL in event.payload.get("labels", [])
            or event.payload.get("label") == AGENT_READY_LABEL
        )
        if item is None and should_start:
            item = WorkItem(
                project_id=project.id,
                tracker_key=key,
                title=event.payload.get("title", key),
                body_snapshot=event.payload.get("body", ""),
                url=event.payload.get("url"),
                state="inbox",
            )
            session.add(item)
            await session.flush()
        if item is None:
            continue
        if should_start:
            workflow_id = interpreter_id(project.slug, key)
            await get_temporal().start_interpreter(
                workflow_id,
                {
                    "project_id": project.id,
                    "project_slug": project.slug,
                    "work_item_id": item.id,
                    "tracker_key": key,
                },
            )
            item.temporal_wf_id = workflow_id
        await deliver_inbound(project.slug, key, event)
        if event.type == InboundEventType.PR_MERGED:
            await get_temporal().signal(
                train_id(project.slug, "prod"),
                "merged",
                {
                    "work_item_key": key,
                    "sha": event.payload.get("sha", ""),
                    "merged_at": utcnow().isoformat(),  # borne de départ du délai de livraison
                    "risk": item.risk,
                    "labels": event.payload.get("labels", []),
                    "pr_url": event.payload.get("pr_url"),
                    "title": item.title,
                },
            )
        delivered += 1
    return delivered


# ───────────────────────────── GitHub ─────────────────────────────


@router.post(
    "/github", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="githubWebhook"
)
async def github_webhook(
    request: Request,
    session: Db,
    response: Response,
    x_github_event: Annotated[str, Header()] = "",
    x_github_delivery: Annotated[str, Header()] = "",
    x_hub_signature_256: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    settings = get_settings()
    if settings.github_webhook_secret and not verify_github_signature(
        settings.github_webhook_secret, body, x_hub_signature_256
    ):
        raise unauthorized("signature HMAC invalide")
    if await _already_seen(session, "github", x_github_delivery or body_digest(body), x_github_event, body):
        return WebhookAck(accepted=True, duplicate=True)

    from choregos_adapters.tracker.github_events import parse_github_event

    events = parse_github_event(x_github_event, x_github_delivery, json.loads(body or b"{}"))
    delivered = await _dispatch(session, events)
    response.status_code = status.HTTP_202_ACCEPTED
    return WebhookAck(accepted=True, events=delivered)


# ───────────────────────────── Tekton ─────────────────────────────


@router.post(
    "/tekton", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="tektonWebhook"
)
async def tekton_webhook(
    request: Request,
    session: Db,
    ce_type: Annotated[str, Header()] = "",
    ce_id: Annotated[str, Header()] = "",
) -> WebhookAck:
    """CloudEvents `dev.tekton.event.pipelinerun.{successful,failed}.v1`."""
    body = await request.body()
    if await _already_seen(session, "tekton", ce_id or body_digest(body), ce_type, body):
        return WebhookAck(accepted=True, duplicate=True)
    payload = json.loads(body or b"{}")
    run = payload.get("pipelineRun", payload)
    labels = (run.get("metadata", {}) or {}).get("labels", {})
    mapping = {
        "dev.tekton.event.pipelinerun.successful.v1": InboundEventType.CI_SUCCEEDED,
        "dev.tekton.event.pipelinerun.failed.v1": InboundEventType.CI_FAILED,
        "dev.tekton.event.pipelinerun.started.v1": InboundEventType.CI_STARTED,
    }
    event = InboundEvent(
        type=mapping.get(ce_type, InboundEventType.CI_STARTED),
        source="tekton",
        delivery_id=ce_id or body_digest(body),
        project_slug=labels.get("choregos/project"),
        work_item_key=labels.get("choregos/work-item"),
        payload={
            "pipeline_run": (run.get("metadata", {}) or {}).get("name"),
            "sha": labels.get("choregos/sha"),
            "run_id": labels.get("choregos/run-id"),
        },
    )
    delivered = await _dispatch(session, [event])
    return WebhookAck(accepted=True, events=delivered)


# ───────────────────────────── Argo CD ─────────────────────────────


@router.post(
    "/argocd", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="argocdWebhook"
)
async def argocd_webhook(
    request: Request,
    session: Db,
    x_choregos_secret: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    settings = get_settings()
    if not verify_shared_secret(settings.generic_webhook_secret, x_choregos_secret):
        raise unauthorized("secret partagé invalide")
    payload = json.loads(body or b"{}")
    app = payload.get("app", payload.get("application", ""))
    health = str(payload.get("health", payload.get("status", ""))).lower()
    kind = InboundEventType.CD_SYNCED if health in {"healthy", "synced"} else InboundEventType.CD_DEGRADED
    if payload.get("rollout") == "aborted":
        kind = InboundEventType.ROLLOUT_ABORTED
    elif payload.get("rollout") == "completed":
        kind = InboundEventType.ROLLOUT_COMPLETED
    delivery = payload.get("delivery_id", body_digest(body))
    if await _already_seen(session, "argocd", delivery, str(kind), body):
        return WebhookAck(accepted=True, duplicate=True)
    event = InboundEvent(
        type=kind,
        source="argocd",
        delivery_id=delivery,
        project_slug=payload.get("project"),
        payload={"app": app, "revision": payload.get("revision"), "env": payload.get("env")},
    )
    project = await _project_for(session, event.project_slug)
    if project is not None and event.payload.get("env"):
        await get_temporal().signal(
            train_id(project.slug, str(event.payload["env"])), "deploy_event", event.model_dump(mode="json")
        )
    return WebhookAck(accepted=True, events=1)


# ───────────────────────────── Alertmanager ─────────────────────────────


@router.post(
    "/alertmanager",
    response_model=WebhookAck,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="alertmanagerWebhook",
)
async def alertmanager_webhook(
    request: Request,
    session: Db,
    x_choregos_secret: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    settings = get_settings()
    if not verify_shared_secret(settings.generic_webhook_secret, x_choregos_secret):
        raise unauthorized("secret partagé invalide")
    payload = json.loads(body or b"{}")
    alerts = payload.get("alerts", [])
    count = 0
    for alert in alerts:
        labels = alert.get("labels", {})
        delivery = alert.get("fingerprint", body_digest(body)) + alert.get("status", "")
        if await _already_seen(session, "alertmanager", delivery, alert.get("status", ""), body):
            continue
        event = InboundEvent(
            type=InboundEventType.ALERT_FIRED
            if alert.get("status") == "firing"
            else InboundEventType.ALERT_RESOLVED,
            source="alertmanager",
            delivery_id=delivery,
            project_slug=labels.get("project"),
            payload={
                "alertname": labels.get("alertname"),
                "severity": labels.get("severity"),
                "labels": labels,
            },
        )
        project = await _project_for(session, event.project_slug)
        if project is not None:
            await get_temporal().signal(f"mem-{project.slug}", "alert", event.model_dump(mode="json"))
        count += 1
    return WebhookAck(accepted=True, events=count)


# ───────────────────────────── Jira / GitLab (S13-04) ─────────────────────────────


@router.post(
    "/jira", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="jiraWebhook"
)
async def jira_webhook(
    request: Request,
    session: Db,
    x_choregos_secret: Annotated[str, Header()] = "",
    x_atlassian_webhook_identifier: Annotated[str, Header()] = "",
) -> WebhookAck:
    """Jira Cloud ne signe pas ses webhooks : le secret partagé est la seule barrière."""
    body = await request.body()
    if not verify_shared_secret(get_settings().generic_webhook_secret, x_choregos_secret):
        raise unauthorized("secret partagé invalide")
    delivery = x_atlassian_webhook_identifier or body_digest(body)
    if await _already_seen(session, "jira", delivery, "jira", body):
        return WebhookAck(accepted=True, duplicate=True)

    from choregos_adapters.tracker.jira_events import parse_jira_event

    events = parse_jira_event(delivery, json.loads(body or b"{}"))
    delivered = await _dispatch(session, events)
    return WebhookAck(accepted=True, events=delivered)


@router.post(
    "/gitlab", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="gitlabWebhook"
)
async def gitlab_webhook(
    request: Request,
    session: Db,
    x_gitlab_token: Annotated[str, Header()] = "",
    x_gitlab_event: Annotated[str, Header()] = "",
    x_gitlab_event_uuid: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    if not verify_shared_secret(get_settings().generic_webhook_secret, x_gitlab_token):
        raise unauthorized("jeton GitLab invalide")
    delivery = x_gitlab_event_uuid or body_digest(body)
    if await _already_seen(session, "gitlab", delivery, x_gitlab_event, body):
        return WebhookAck(accepted=True, duplicate=True)

    from choregos_adapters.tracker.gitlab_events import parse_gitlab_event

    events = parse_gitlab_event(x_gitlab_event, delivery, json.loads(body or b"{}"))
    delivered = await _dispatch(session, events)
    return WebhookAck(accepted=True, events=delivered)
