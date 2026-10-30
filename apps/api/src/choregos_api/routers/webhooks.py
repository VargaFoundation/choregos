# SPDX-License-Identifier: Apache-2.0
"""Webhooks entrants : vérification de signature, dédup, normalisation, réponse < 500 ms.

Le traitement lourd n'a pas lieu ici : on normalise en `InboundEvent`, on déduplique
par `delivery_id`, et on signale Temporal. Tout le reste est asynchrone.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from choregos_contracts import InboundEvent, InboundEventType
from choregos_core import utcnow
from choregos_core.dsl.trains import approbation_du_train, env_du_train, train_apres_fusion
from fastapi import APIRouter, Header, Path, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db.models import Connector, OrgConnector, Project, WebhookDelivery, WorkItem
from ..deps import DbPlateforme
from ..errors import not_found, unauthorized
from ..logging import get_logger
from ..schemas import WebhookAck
from ..security import body_digest, verify_github_signature, verify_shared_secret
from ..services import (
    Naissance,
    noter_les_etiquettes,
    nouveau_ticket,
    received_events,
    workflow_du_ticket,
    workflow_model,
)
from ..services.initiator import initiator
from ..temporal import deliver_inbound, get_temporal, interpreter_id, train_id

router = APIRouter(tags=["webhooks"], prefix="/webhooks")
logger = get_logger("choregos.webhooks")

AGENT_READY_LABEL = "agent-ready"
#: Les événements de ticket qui portent la liste COURANTE de ses étiquettes : `labeled` (posée ou
#: retirée, `removed`), et `updated` — GitLab n'émet rien d'autre quand une étiquette est retirée.
ETIQUETTES_A_JOUR = frozenset({InboundEventType.ITEM_LABELED, InboundEventType.ITEM_UPDATED})


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


def _host(url: Any) -> str:
    """The host of an address, lowercased; empty for anything that is not one."""
    from urllib.parse import urlsplit

    return (urlsplit(url).hostname or "").lower() if isinstance(url, str) else ""


async def _jira_project_for(session: Any, payload: dict[str, Any]) -> tuple[Project | None, bool]:
    """The project a Jira delivery is for, by its tracker connector's `project_key`.

    A Jira project key (`XXX` in `XXX-42`) is not the Choregos project's slug: routing by the slug
    dropped every event of a project named otherwise, with a log line as the only trace. Two
    projects whose connectors hold the same key are told apart by the Jira site the issue lives on
    (`issue.self`); if that does not settle it, nothing is routed. Returns the project and whether a
    connector decided: `(None, False)` lets the caller fall back to the slug, `(None, True)` does not.
    """
    issue = payload.get("issue") if isinstance(payload, dict) else None
    if not isinstance(issue, dict):
        return None, False
    key = issue.get("key")
    if not isinstance(key, str) or "-" not in key:
        return None, False
    prefix = key.rsplit("-", 1)[0].upper()
    rows = (
        await session.execute(
            select(Connector, Project)
            .join(Project, Project.id == Connector.project_id)
            .where(Connector.kind == "tracker", Connector.type == "jira")
        )
    ).all()
    matches = [
        (connector, project)
        for connector, project in rows
        if str((connector.config or {}).get("project_key", "")).upper() == prefix
    ]
    if len(matches) > 1 and (site := _host(issue.get("self"))):
        matches = [m for m in matches if _host((m[0].config or {}).get("base_url")) == site]
    if len(matches) == 1:
        return matches[0][1], True
    if matches:
        logger.warning("jira event matches several projects, not routed", project_key=prefix)
        return None, True
    return None, False


async def _dispatch(session: Any, events: list[InboundEvent], project_of: Project | None = None) -> int:
    """Route each event: a signal to its work item's workflow, or a start on `agent-ready`.

    `project_of`: the project, already known from the organisation that verified the delivery (a plugin
    server's webhook, S43-07): never looked up by slug across organisations."""
    delivered = 0
    for event in events:
        project = project_of or await _project_for(session, event.project_slug)
        if project is None:
            logger.info("événement sans projet connu", event_type=str(event.type), slug=event.project_slug)
            continue
        key = event.work_item_key
        if key is None:
            received_events.record(session, project, event, "ignored_no_item_key")
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
        if item is None and not should_start and _starts_by_rule(project, event):
            should_start = True  # a project rule, not the label (S51-03)
        created = False
        if item is None and should_start:
            created = True
            # Né dans le workflow que désignent ses étiquettes ou son type (ADR 0031) — plus en
            # `inbox` en dur, qui tuait le ticket sur un workflow commençant ailleurs (#173).
            item = await nouveau_ticket(
                session,
                project,
                Naissance(
                    tracker_key=key,
                    title=event.payload.get("title", key),
                    body=event.payload.get("body", ""),
                    url=event.payload.get("url"),
                    labels=tuple(event.payload.get("labels") or []),
                    item_type=event.payload.get("item_type"),
                    initiated_by=initiator("tracker", event.source),
                ),
            )
        if item is None:
            received_events.record(session, project, event, "ignored_not_agent_ready")
            continue
        etiquettes = event.payload.get("labels")
        if event.type in ETIQUETTES_A_JOUR and isinstance(etiquettes, list):
            # L'état courant des étiquettes, posé ou retiré : la voie express les lit (#279).
            noter_les_etiquettes(item, etiquettes)
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
            await _embarquer(session, project, item, event)
        received_events.record(
            session, project, event, _outcome(event, created, should_start, etiquettes), item
        )
        delivered += 1
    return delivered


#: The events that may start an item the project does not hold yet, by a start rule (S51-03).
STARTS_BY_RULE = frozenset(
    {
        InboundEventType.ITEM_CREATED,
        InboundEventType.ITEM_UPDATED,
        InboundEventType.ITEM_LABELED,
        InboundEventType.ITEM_MOVED,
    }
)


def _starts_by_rule(project: Project, event: InboundEvent) -> bool:
    """Whether one of the project's `start_rules` holds for the item the event describes (S51-03,
    ADR 0113): a ServiceNow incident in the desk's queue starts without an `agent-ready` label. Every
    condition of a rule must hold; a value the event does not carry never matches."""
    if event.type not in STARTS_BY_RULE:
        return False
    from ..services.definitions import project_config

    rules = project_config(project).start_rules
    if not rules:
        return False
    payload = event.payload or {}
    labels = {str(label) for label in payload.get("labels") or [] if label}
    if isinstance(payload.get("label"), str) and not payload.get("removed"):
        labels.add(payload["label"])
    item_type = payload.get("item_type")
    state = payload.get("to_status") or payload.get("state")
    return any(
        rule.matches(labels, str(item_type) if item_type else None, str(state) if state else None)
        for rule in rules
    )


def _outcome(event: InboundEvent, created: bool, started: bool, labels: Any) -> str:
    """What `_dispatch` did with an event that reached a work item (S42-06f)."""
    if created:
        return "created_work_item"
    if started:
        return "started"
    if event.type == InboundEventType.PR_MERGED:
        return "boarded"
    if event.type in ETIQUETTES_A_JOUR and isinstance(labels, list):
        return "labels_updated"
    return "signalled"


async def _embarquer(session: Any, project: Project, item: WorkItem, event: InboundEvent) -> None:
    """Le ticket fusionné monte dans le train que SON workflow prend après la fusion, avec l'approbation
    qu'il y exige (ADR 0041). Tout ticket fusionné montait dans le train de PROD : une étude qui
    fusionne un ADR partait en production, une livraison prudente sautait son staging."""
    workflow = workflow_model(await workflow_du_ticket(session, item))
    train = train_apres_fusion(workflow)
    if train is None:
        return
    # Les étiquettes de la PR ET celles du ticket : un ticket `hotfix` prend la voie express, que ce
    # soit ce webhook ou l'interpréteur qui l'embarque le premier (#279).
    etiquettes = [*event.payload.get("labels", []), *(item.documents or {}).get("labels", [])]
    await get_temporal().signal(
        train_id(project.slug, env_du_train(train)),
        "merged",
        {
            "work_item_key": item.tracker_key,
            "sha": event.payload.get("sha", ""),
            "merged_at": utcnow().isoformat(),  # borne de départ du délai de livraison
            "risk": item.risk,
            "labels": list(dict.fromkeys(etiquettes)),
            "pr_url": event.payload.get("pr_url"),
            "title": item.title,
            "approval": dict(approbation_du_train(workflow, train)),
        },
    )


# ───────────────────────────── GitHub ─────────────────────────────


@router.post(
    "/github", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="githubWebhook"
)
async def github_webhook(
    request: Request,
    session: DbPlateforme,
    response: Response,
    x_github_event: Annotated[str, Header()] = "",
    x_github_delivery: Annotated[str, Header()] = "",
    x_hub_signature_256: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    settings = get_settings()
    if not settings.github_webhook_secret:
        # Sans secret, la signature n'était pas vérifiée du tout : n'importe qui pouvait
        # injecter des événements de tracker. Toléré seulement là où il n'y a pas de GitHub.
        if settings.env in {"staging", "prod"}:
            raise unauthorized("GitHub webhook not configured: CHOREGOS_GITHUB_WEBHOOK_SECRET is missing")
    elif not verify_github_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise unauthorized("invalid HMAC signature")
    if await _already_seen(session, "github", x_github_delivery or body_digest(body), x_github_event, body):
        return WebhookAck(accepted=True, duplicate=True)

    if x_github_event in {"installation", "installation_repositories"}:
        # The App's own events (ADR 0073): they keep `github_installations` in step and are no work item.
        from ..services.github_app import apply_installation_event

        await apply_installation_event(session, x_github_event, json.loads(body or b"{}"))
        return WebhookAck(accepted=True)

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
    session: DbPlateforme,
    ce_type: Annotated[str, Header()] = "",
    ce_id: Annotated[str, Header()] = "",
    x_choregos_secret: Annotated[str, Header()] = "",
) -> WebhookAck:
    """CloudEvents `dev.tekton.event.pipelinerun.{successful,failed}.v1`.

    Authentifié par le secret partagé, en en-tête `X-Choregos-Secret` OU dans l'URL du puits
    (`?jeton=`) : le puits CloudEvents de Tekton se règle par une URL et n'ajoute pas d'en-tête. Il
    n'y avait AUCUNE vérification — sans conséquence tant que la RLS cachait tout à cette route sur
    PostgreSQL, un chemin d'écriture anonyme dès qu'elle voit les projets (« CI réussie » sur
    n'importe quel ticket).
    """
    settings = get_settings()
    fourni = x_choregos_secret or request.query_params.get("jeton", "")
    if not settings.generic_webhook_secret:
        if settings.env in {"staging", "prod"}:
            raise unauthorized("Tekton webhook not configured: CHOREGOS_GENERIC_WEBHOOK_SECRET is missing")
    elif not verify_shared_secret(settings.generic_webhook_secret, fourni):
        raise unauthorized("invalid shared secret")
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
    session: DbPlateforme,
    x_choregos_secret: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    settings = get_settings()
    if not verify_shared_secret(settings.generic_webhook_secret, x_choregos_secret):
        raise unauthorized("invalid shared secret")
    payload = json.loads(body or b"{}")
    app = payload.get("app", payload.get("application", ""))
    health = str(payload.get("health", payload.get("status", ""))).lower()
    kind = _genre_argocd(health, payload.get("rollout"))
    delivery = payload.get("delivery_id", body_digest(body))
    if await _already_seen(session, "argocd", delivery, str(kind or f"argocd.{health or 'unknown'}"), body):
        return WebhookAck(accepted=True, duplicate=True)
    if kind is None:
        # Une santé de passage (`progressing`, `suspended`, `missing`, `unknown`…) n'est ni une
        # livraison ni une alerte : la signaler en `cd.app.degraded` faisait revenir en arrière un
        # départ sain, depuis que le train écoute le CD (S22-18, #349).
        return WebhookAck(accepted=True, events=0)
    event = InboundEvent(
        type=kind,
        source="argocd",
        delivery_id=delivery,
        project_slug=payload.get("project"),
        payload={
            "app": app,
            "revision": payload.get("revision"),
            "env": payload.get("env"),
            "health": health,
        },
    )
    project = await _project_for(session, event.project_slug)
    if project is not None and event.payload.get("env"):
        await get_temporal().signal(
            train_id(project.slug, str(event.payload["env"])), "deploy_event", event.model_dump(mode="json")
        )
        received_events.record(session, project, event, "signalled")
    return WebhookAck(accepted=True, events=1)


def _genre_argocd(health: str, rollout: Any) -> InboundEventType | None:
    """Ce qu'une notification Argo CD annonce : livré, dégradé, rollout fini ou abandonné — ou
    rien, pour une santé de passage. Seul `degraded` est une alerte (#349)."""
    if rollout == "aborted":
        return InboundEventType.ROLLOUT_ABORTED
    if rollout == "completed":
        return InboundEventType.ROLLOUT_COMPLETED
    if health in {"healthy", "synced"}:
        return InboundEventType.CD_SYNCED
    if health == "degraded":
        return InboundEventType.CD_DEGRADED
    return None


# ───────────────────────────── Alertmanager ─────────────────────────────


@router.post(
    "/alertmanager",
    response_model=WebhookAck,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="alertmanagerWebhook",
)
async def alertmanager_webhook(
    request: Request,
    session: DbPlateforme,
    x_choregos_secret: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    settings = get_settings()
    if not verify_shared_secret(settings.generic_webhook_secret, x_choregos_secret):
        raise unauthorized("invalid shared secret")
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
            received_events.record(session, project, event, "signalled")
        count += 1
    return WebhookAck(accepted=True, events=count)


# ───────────────────────────── Jira / GitLab (S13-04) ─────────────────────────────


@router.post(
    "/jira", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="jiraWebhook"
)
async def jira_webhook(
    request: Request,
    session: DbPlateforme,
    x_choregos_secret: Annotated[str, Header()] = "",
    x_atlassian_webhook_identifier: Annotated[str, Header()] = "",
) -> WebhookAck:
    """Jira Cloud ne signe pas ses webhooks : le secret partagé est la seule barrière."""
    body = await request.body()
    if not verify_shared_secret(get_settings().generic_webhook_secret, x_choregos_secret):
        raise unauthorized("invalid shared secret")
    delivery = x_atlassian_webhook_identifier or body_digest(body)
    if await _already_seen(session, "jira", delivery, "jira", body):
        return WebhookAck(accepted=True, duplicate=True)

    from choregos_adapters.tracker.jira_events import parse_jira_event

    payload = json.loads(body or b"{}")
    events = parse_jira_event(delivery, payload)
    project, decided = await _jira_project_for(session, payload)
    if decided and project is None:
        return WebhookAck(accepted=True, events=0)
    delivered = await _dispatch(session, events, project_of=project)
    return WebhookAck(accepted=True, events=delivered)


@router.post(
    "/gitlab", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="gitlabWebhook"
)
async def gitlab_webhook(
    request: Request,
    session: DbPlateforme,
    x_gitlab_token: Annotated[str, Header()] = "",
    x_gitlab_event: Annotated[str, Header()] = "",
    x_gitlab_event_uuid: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    if not verify_shared_secret(get_settings().generic_webhook_secret, x_gitlab_token):
        raise unauthorized("invalid GitLab token")
    delivery = x_gitlab_event_uuid or body_digest(body)
    if await _already_seen(session, "gitlab", delivery, x_gitlab_event, body):
        return WebhookAck(accepted=True, duplicate=True)

    from choregos_adapters.tracker.gitlab_events import parse_gitlab_event

    events = parse_gitlab_event(x_gitlab_event, delivery, json.loads(body or b"{}"))
    delivered = await _dispatch(session, events)
    return WebhookAck(accepted=True, events=delivered)


# ───────────────────────────── A plugin's tracker server (S43-07) ─────────────────────────────

PluginName = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
OrgSlug = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
#: Headers a delivery never forwards to the server: the caller's own credentials and transport.
_NOT_FORWARDED = frozenset({"authorization", "cookie", "host", "content-length", "connection"})


async def _tracker_servers(session: Any, org: str, plugin: str, server: str) -> list[OrgConnector]:
    """The organisation's running connector of `plugin`'s server `server`, when it is a tracker with a
    webhook. The URL names the organisation: a delivery is never shown to another organisation's server."""
    from ..db.org_aliases import organisation_by_slug_or_alias
    from ..services.plugin_servers import DEPLOYED

    # A renamed organisation keeps its webhook URL: an old slug is an alias (ADR 0090).
    owner = await organisation_by_slug_or_alias(session, org)
    if owner is None:
        return []
    rows = (
        await session.execute(
            select(OrgConnector).where(OrgConnector.org_id == owner.id, OrgConnector.name == server)
        )
    ).scalars()
    found = []
    for row in rows:
        declared = row.plugin_server or {}
        if (declared.get("plugin"), declared.get("server")) != (plugin, server):
            continue
        if "tracker" in (declared.get("implements") or []) and declared.get("webhook"):
            found.append(row)
    return sorted((r for r in found if r.status == DEPLOYED), key=lambda r: str(r.org_id))


async def _projects_on(session: Any, org_id: str, plugin: str, server: str) -> list[Project]:
    """The organisation's projects whose tracker is this plugin server."""
    rows = await session.execute(
        select(Project, Connector)
        .join(Connector, Connector.project_id == Project.id)
        .where(Project.org_id == org_id, Connector.kind == "tracker", Connector.type == "plugin")
        .order_by(Project.slug)
    )
    return [
        project
        for project, connector in rows.all()
        if ((connector.config or {}).get("plugin"), (connector.config or {}).get("server"))
        == (plugin, server)
    ]


async def _project_of_item(session: Any, projects: list[Project], key: str) -> Project | None:
    """The project that holds `key`; else the organisation's only project on this tracker."""
    if not projects:
        return None
    held = (
        await session.execute(
            select(WorkItem.project_id).where(
                WorkItem.project_id.in_([p.id for p in projects]), WorkItem.tracker_key == key
            )
        )
    ).scalars()
    ids = set(held)
    owners = [p for p in projects if p.id in ids]
    if len(owners) == 1:
        return owners[0]
    return projects[0] if len(projects) == 1 and not owners else None


@router.post(
    "/plugins/{org}/{plugin}/{server}",
    response_model=WebhookAck,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="pluginWebhook",
)
async def plugin_webhook(
    org: OrgSlug, plugin: PluginName, server: PluginName, request: Request, session: DbPlateforme
) -> WebhookAck:
    """A delivery for a plugin server that is a tracker (plan S43 Decision 3).

    The headers and body go to the server's `tracker_parse_event` through `/mcp` with the server's
    bearer token — the only door of the server the platform authenticates (ADR 0096 §6) — and the
    server says whether the delivery is its own (its secret) and which items changed. Each event is
    de-duplicated on the server's `delivery_id`, then its item is RE-READ with `tracker_fetch_item`:
    nothing of the payload but the key is trusted. The URL names the organisation, so only that
    organisation's server sees the delivery. 404: the organisation runs no tracker server of that
    name; 401: the server does not recognise the delivery."""
    from choregos_adapters import build
    from choregos_adapters.errors import AdapterError

    from ..services.plugin_tracker import pin_plugin_tracker

    candidates = await _tracker_servers(session, org, plugin, server)
    if not candidates:
        raise not_found("Plugin tracker server", f"{org}/{plugin}/{server}")
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _NOT_FORWARDED}
    for connector in candidates:
        config = await pin_plugin_tracker(
            session, connector.org_id, "tracker", "plugin", {"plugin": plugin, "server": server}
        )
        try:
            adapter = build("tracker", "plugin", config)
            verified, events = await adapter.parse_event(headers, body)
        except AdapterError as failed:
            logger.warning("plugin.webhook.server_failed", plugin=plugin, server=server, error=str(failed))
            continue
        if not verified:
            continue
        projects = await _projects_on(session, connector.org_id, plugin, server)
        delivered = duplicates = 0
        for found in events:
            delivery = f"{connector.id}:{found['delivery_id']}"[:255]
            if await _already_seen(session, "plugin", delivery, found["kind"], body):
                duplicates += 1
                continue
            project = await _project_of_item(session, projects, found["item_key"])
            if project is None:
                logger.info("plugin.webhook.unrouted", plugin=plugin, server=server, key=found["item_key"])
                continue
            item = await adapter.fetch_item(found["item_key"])
            event = InboundEvent(
                type=InboundEventType(found["kind"]),
                source=f"plugin:{plugin}/{server}",
                delivery_id=found["delivery_id"],
                project_slug=project.slug,
                work_item_key=item.key,
                payload={
                    "title": item.title,
                    "body": item.body,
                    "url": item.url,
                    "labels": list(item.labels),
                    "item_type": item.item_type,
                    "state": item.state,
                },
            )
            delivered += await _dispatch(session, [event], project_of=project)
        return WebhookAck(
            accepted=True, events=delivered, duplicate=bool(events) and duplicates == len(events)
        )
    raise unauthorized("no server recognised the delivery")
