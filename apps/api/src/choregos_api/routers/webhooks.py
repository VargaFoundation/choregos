# SPDX-License-Identifier: Apache-2.0
"""Inbound webhooks: signature check, dedup, normalisation, response < 500 ms.

Heavy processing does not happen here: we normalise into an `InboundEvent`, deduplicate
by `delivery_id`, and signal Temporal. Everything else is asynchronous.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from choregos_adapters.errors import AdapterError, UpstreamError
from choregos_contracts import InboundEvent, InboundEventType
from choregos_core import utcnow
from choregos_core.dsl.trains import train_after_merge, train_approval, train_env
from fastapi import APIRouter, Header, Path, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..audit import record
from ..config import get_settings
from ..db.models import Connector, OrgConnector, Project, TrackerIdentity, User, WebhookDelivery, WorkItem
from ..deps import PlatformDb, principal_for_user, resolve_project
from ..errors import ApiError, not_found, unauthorized
from ..logging import get_logger
from ..schemas import WebhookAck
from ..security import body_digest, verify_github_signature, verify_shared_secret
from ..services import (
    WorkItemBirth,
    new_work_item,
    received_events,
    record_labels,
    tickets,
    work_item_workflow,
    workflow_model,
)
from ..services.decision_ledger import ended_issue_reopened, observe_reopening
from ..services.initiator import initiator
from ..services.recurrence import restart_work_item, says_back_to_work
from ..services.routing import with_tracker
from ..services.work_item_controls import apply_control
from ..temporal import (
    InterpreterNotRunningError,
    MemoryLoopNotRunningError,
    deliver_inbound,
    get_temporal,
    interpreter_id,
    signal_memory_loop,
    train_id,
)

router = APIRouter(tags=["webhooks"], prefix="/webhooks")
logger = get_logger("choregos.webhooks")

AGENT_READY_LABEL = "agent-ready"
#: The issue events that carry the CURRENT list of its labels: `labeled` (added or removed,
#: `removed`), and `updated`: GitLab emits nothing else when a label is removed.
LABEL_STATE_EVENTS = frozenset({InboundEventType.ITEM_LABELED, InboundEventType.ITEM_UPDATED})


async def _already_seen(session: Any, source: str, delivery_id: str, event_type: str, body: bytes) -> bool:
    """Dedup by (source, delivery_id): a replay restarts nothing."""
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
    except IntegrityError:  # race between two replicas
        await session.rollback()
        return True
    return False


async def _project_for(session: Any, slug_or_repo: str | None) -> Project | None:
    """The project an event targets: by its repository first, by its slug if that is unique.

    Two organisations can have a project `billing-api`; taking "the first" routed one tenant's
    event to the other. The full repository name (`owner/repo`) removes the ambiguity; without
    it, a slug present twice routes nothing, and says so.
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
            logger.warning("ambiguous event between several projects, not routed", slug=slug_or_repo)
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
            # An archived project keeps its connector, but no longer takes deliveries: holding the
            # same key as its successor, it made the routing ambiguous and nothing was routed.
            .where(Connector.kind == "tracker", Connector.type == "jira", Project.status != "archived")
        )
    ).all()
    matches = [
        (connector, project)
        for connector, project in rows
        if str((connector.config or {}).get("project_key", "")).upper() == prefix
    ]
    if len(matches) > 1 and (site := _host(issue.get("self"))):
        matches = [m for m in matches if _host((m[0].config or {}).get("base_url")) == site]
    if len(matches) > 1:
        matches = _by_labels(matches, issue)
    if len(matches) == 1:
        return matches[0][1], True
    if matches:
        logger.warning("jira event matches several projects, not routed", project_key=prefix)
        return None, True
    return None, False


def _by_labels(matches: list[Any], issue: dict[str, Any]) -> list[Any]:
    """Projects sharing one Jira project are told apart by labels (S59-03): an issue goes to the
    connectors whose `match_labels` it carries; one that carries none of them goes to the connectors that
    claim no label (the project that takes the rest)."""
    raw = issue.get("fields")
    fields: dict[str, Any] = raw if isinstance(raw, dict) else {}
    labels = {str(label) for label in (fields.get("labels") or []) if isinstance(label, str)}

    def claimed(match: Any) -> set[str]:
        return {str(label) for label in ((match[0].config or {}).get("match_labels") or [])}

    by_label = [m for m in matches if claimed(m) & labels]
    return by_label or [m for m in matches if not claimed(m)]


#: Verbs a tracker comment can carry. None is honoured (ADR 0102): a tracker login is not a principal.
DECISION_VERBS = frozenset({"approve", "reject", "answer", "scope"})
CONTROL_VERBS = frozenset({"pause", "resume", "stop"})
COMMAND_MARKER = "<!-- choregos:command -->"


LINK_HINT = "an organisation administrator can link your GitHub account to your member"


async def _control_from_a_signed_comment(
    session: Any, project: Project, item: WorkItem, event: InboundEvent, verb: str
) -> str | None:
    """Apply pause, resume or stop with the rights of the member whose GitHub user id sent the comment.

    Honoured only when the delivery is a GitHub one verified by its HMAC (a secret is configured, or the
    delivery was refused) and the commenter's numeric id is linked to a member of the project's
    organisation. A login, a username or a display name is never matched (ADR 0102). Returns the reply,
    or None when the command is not honoured."""
    by_id = str(event.payload.get("by_id") or "")
    if event.source != "github" or not get_settings().github_webhook_secret or not by_id:
        return None
    identity = (
        await session.execute(
            select(TrackerIdentity).where(
                TrackerIdentity.org_id == project.org_id,
                TrackerIdentity.tracker == "github",
                TrackerIdentity.external_id == by_id,
            )
        )
    ).scalar_one_or_none()
    user = await session.get(User, identity.user_id) if identity is not None else None
    if user is None:
        return None
    # Not narrowed and not attributed: a delivery with several commands must not leave the session
    # scoped to, or attributed to, the first member.
    principal = await principal_for_user(session, user, narrow=False)
    _, org_slug = await resolve_project(session, project.id)
    try:
        await apply_control(
            session,
            principal,
            project,
            item,
            org_slug,
            verb,  # type: ignore[arg-type]
            f"/choregos {verb} on the tracker",
            "tracker",
        )
    except ApiError as refused:
        return f"`/choregos {verb}` was not applied: {refused.detail}"
    except InterpreterNotRunningError:
        return f"`/choregos {verb}` was not applied: the work item is not running"
    return f"Done: {verb} by {user.email}"


async def _answer_tracker_command(
    session: Any, project: Project, item: WorkItem, event: InboundEvent
) -> None:
    """Answer a `/choregos ...` comment on the tracker with a console link, and audit it.

    Nothing reaches the interpreter. The reply never starts with the command, so it is not read as one,
    and a tracker that cannot be reached or built never fails the delivery."""
    verb = str(event.payload.get("verb") or "")
    by = event.payload.get("by")
    base = get_settings().public_url.rstrip("/")
    answered_by_a_member = False
    if verb in DECISION_VERBS:
        link = f"{base}/inbox"
        text = (
            f"`/choregos {verb}` is not taken from a comment: a decision is made in the console, "
            f"after a fresh sign-in: {link}"
        )
    else:
        done = await _control_from_a_signed_comment(session, project, item, event, verb)
        answered_by_a_member = done is not None
        link = f"{base}/p/{project.slug}/items/{item.id}"
        text = done or (
            f"`/choregos {verb}` is not taken from this comment: pause, resume and stop are done from "
            f"the work item in the console: {link}. {LINK_HINT[0].upper()}{LINK_HINT[1:]}."
        )
    try:
        _, tracker = await tickets.project_tracker(session, project)
        if tracker is not None:
            await tracker.comment(item.tracker_key, f"{COMMAND_MARKER}\n{text}")
    except (ApiError, AdapterError, UpstreamError) as failure:
        logger.warning(
            "tracker command not answered", project=project.slug, work_item=item.id, reason=str(failure)
        )
    if answered_by_a_member:
        return
    await record(
        session,
        None,
        "tracker.command.redirected",
        org_id=project.org_id,
        target_type="work_item",
        target_id=item.id,
        verb=verb,
        by=by,
        source=event.source,
    )


#: Reads what the tracker says about a ticket (`fields.tracker`, S51-04) from the tracker itself.
TrackerOf = Callable[[Project, str], Awaitable[dict[str, Any] | None]]


async def _dispatch(
    session: Any,
    events: list[InboundEvent],
    project_of: Project | None = None,
    tracker_of: TrackerOf | None = None,
) -> int:
    """Route each event: a signal to its work item's workflow, or a start on `agent-ready`.

    `project_of`: the project, already known from the organisation that verified the delivery (a plugin
    server's webhook, S43-07): never looked up by slug across organisations.

    `tracker_of`: what the tracker says about the ticket, re-read from the tracker (never from the
    payload): a work item born of the event carries it as `fields.tracker`, and an existing one has it
    replaced, so a requester or an answer changed in the tracker reaches the next stage (S51-04)."""
    delivered = 0
    for event in events:
        project = project_of or await _project_for(session, event.project_slug)
        if project is None:
            logger.info("event without a known project", event_type=str(event.type), slug=event.project_slug)
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
            should_start = True  # a project rule, not the label (S56-03)
        created = False
        tracker: dict[str, Any] | None = None
        if tracker_of is not None and (item is not None or should_start):
            tracker = await tracker_of(project, key)
        if item is None and should_start:
            created = True
            # Born in the workflow its labels or its type designate (ADR 0031), no longer in a
            # hard-coded `inbox`, which killed the ticket on a workflow starting elsewhere (#173).
            item = await new_work_item(
                session,
                project,
                WorkItemBirth(
                    tracker_key=key,
                    title=event.payload.get("title", key),
                    body=event.payload.get("body", ""),
                    url=event.payload.get("url"),
                    labels=tuple(event.payload.get("labels") or []),
                    item_type=event.payload.get("item_type"),
                    reporter=event.payload.get("reporter"),
                    initiated_by=initiator("tracker", event.source),
                    tracker=tracker,
                ),
            )
        if item is None:
            received_events.record(session, project, event, "ignored_not_agent_ready")
            continue
        if tracker is not None and not created:
            item.fields = with_tracker(dict(item.fields or {}), tracker)
        label_list = _current_labels(item, event)
        if not created and await _came_back(session, project, item, event, should_start):
            # An ended work item whose ticket came back starts a new pass (C1): its decisions were not
            # right (ADR 0121 §6). The event is the restart: it is not also delivered to the new pass.
            received_events.record(session, project, event, "reopened", item)
            delivered += 1
            continue
        # An ended work item whose issue is open again: its decisions were not right (ADR 0121 §6).
        await observe_reopening(session, item, str(event.type), event.payload)
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
        if event.type == InboundEventType.ITEM_MOVED:
            delivered += _card_move(session, project, event, item, created=created, key=key)
            continue
        if event.type == InboundEventType.HUMAN_DECISION:
            await _answer_tracker_command(session, project, item, event)
            delivered += 1
            continue
        try:
            await deliver_inbound(project.slug, key, event)
        except InterpreterNotRunningError:
            logger.info(
                "event for a work item whose interpreter has ended",
                project=project.slug,
                work_item=item.id,
                event_type=str(event.type),
            )
        if event.type == InboundEventType.PR_MERGED:
            await _board_train(session, project, item, event)
        received_events.record(
            session, project, event, _outcome(event, created, should_start, label_list), item
        )
        delivered += 1
    return delivered


def _current_labels(item: WorkItem, event: InboundEvent) -> Any:
    """The labels the event carries; on an event that carries the CURRENT list, they are recorded on the
    work item, added or removed: the express lane reads them (#279)."""
    label_list = event.payload.get("labels")
    if event.type in LABEL_STATE_EVENTS and isinstance(label_list, list):
        record_labels(item, label_list)
    return label_list


async def _came_back(
    session: Any, project: Project, item: WorkItem, event: InboundEvent, should_start: bool
) -> bool:
    """Whether the event brings an ENDED work item back — its issue reopened (`ended_issue_reopened`:
    GitHub's `reopened`, GitLab's `reopen`, a Jira move back to work), or its start label applied again —
    and this delivery claimed its new pass (C1, `services/recurrence.py`). A second delivery, or another
    door that claimed it first, finds the work item running: false, and the event goes on as usual."""
    if item.closed_at is None:
        return False
    kind = str(event.type)
    if not should_start and not (
        says_back_to_work(kind, event.payload)
        and await ended_issue_reopened(session, item, kind, event.payload)
    ):
        return False
    why = "start label applied again" if should_start else "issue reopened"
    door = f"tracker:{event.source}"
    return await restart_work_item(session, project, item, door=door, reason=why) is not None


#: The events that may start an item the project does not hold yet, by a start rule (S56-03).
STARTS_BY_RULE = frozenset(
    {
        InboundEventType.ITEM_CREATED,
        InboundEventType.ITEM_UPDATED,
        InboundEventType.ITEM_LABELED,
        InboundEventType.ITEM_MOVED,
    }
)


def _card_move(
    session: Any, project: Project, event: InboundEvent, item: WorkItem, *, created: bool, key: str
) -> int:
    """A tracker card move (ADR 0102): never a decision, so never delivered. A move into a start rule's
    state is how the item was born (S56-03): that birth is kept as a received event and counted."""
    if created:
        received_events.record(session, project, event, "created_work_item", item)
        return 1
    # Not counted, not audited, no comment: Choregos's own mirror comes back as moves too.
    logger.info(
        "tracker move not applied: a card move is not a decision (ADR 0102)",
        project=project.slug,
        work_item=key,
        to_status=event.payload.get("to_status"),
    )
    return 0


def _starts_by_rule(project: Project, event: InboundEvent) -> bool:
    """Whether one of the project's `start_rules` holds for the item the event describes (S56-03,
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
    if event.type in LABEL_STATE_EVENTS and isinstance(labels, list):
        return "labels_updated"
    return "signalled"


async def _board_train(session: Any, project: Project, item: WorkItem, event: InboundEvent) -> None:
    """The merged ticket boards the train that ITS workflow takes after the merge, with the approval
    it requires there (ADR 0041). Every merged ticket used to board the PROD train: a study that
    merges an ADR went to production, a careful delivery skipped its staging."""
    workflow = workflow_model(await work_item_workflow(session, item))
    train = train_after_merge(workflow)
    if train is None:
        return
    # The PR's labels AND the ticket's: a `hotfix` ticket takes the express lane, whether this
    # webhook or the interpreter boards it first (#279).
    merged_labels = [*event.payload.get("labels", []), *(item.documents or {}).get("labels", [])]
    await get_temporal().signal(
        train_id(project.slug, train_env(train)),
        "merged",
        {
            "work_item_key": item.tracker_key,
            "sha": event.payload.get("sha", ""),
            "merged_at": utcnow().isoformat(),  # start bound of the delivery delay
            "risk": item.risk,
            "labels": list(dict.fromkeys(merged_labels)),
            "pr_url": event.payload.get("pr_url"),
            "title": item.title,
            "approval": dict(train_approval(workflow, train)),
        },
    )


# ───────────────────────────── GitHub ─────────────────────────────


@router.post(
    "/github", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="githubWebhook"
)
async def github_webhook(
    request: Request,
    session: PlatformDb,
    response: Response,
    x_github_event: Annotated[str, Header()] = "",
    x_github_delivery: Annotated[str, Header()] = "",
    x_hub_signature_256: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    settings = get_settings()
    if not settings.github_webhook_secret:
        # Without a secret, the signature was not verified at all: anyone could inject tracker
        # events. Tolerated only where there is no GitHub.
        if settings.deployed:
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
    session: PlatformDb,
    ce_type: Annotated[str, Header()] = "",
    ce_id: Annotated[str, Header()] = "",
    x_choregos_secret: Annotated[str, Header()] = "",
) -> WebhookAck:
    """CloudEvents `dev.tekton.event.pipelinerun.{successful,failed}.v1`.

    Authenticated by the shared secret, in the `X-Choregos-Secret` header OR in the sink URL
    (`?token=`): Tekton's CloudEvents sink is configured with a URL and adds no header. The former
    spelling of the parameter was removed in 0.24.0 (S45-49): it authenticates nothing. There used
    to be NO check: harmless while row-level security hid everything from this route on PostgreSQL,
    an anonymous write path as soon as it sees the projects ("CI passed" on any work item).
    """
    settings = get_settings()
    supplied = x_choregos_secret or request.query_params.get("token", "")
    if not settings.generic_webhook_secret:
        if settings.deployed:
            raise unauthorized("Tekton webhook not configured: CHOREGOS_GENERIC_WEBHOOK_SECRET is missing")
    elif not verify_shared_secret(settings.generic_webhook_secret, supplied):
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
    session: PlatformDb,
    x_choregos_secret: Annotated[str, Header()] = "",
) -> WebhookAck:
    body = await request.body()
    settings = get_settings()
    if not verify_shared_secret(settings.generic_webhook_secret, x_choregos_secret):
        raise unauthorized("invalid shared secret")
    payload = json.loads(body or b"{}")
    app = payload.get("app", payload.get("application", ""))
    health = str(payload.get("health", payload.get("status", ""))).lower()
    kind = _argocd_kind(health, payload.get("rollout"))
    delivery = payload.get("delivery_id", body_digest(body))
    if await _already_seen(session, "argocd", delivery, str(kind or f"argocd.{health or 'unknown'}"), body):
        return WebhookAck(accepted=True, duplicate=True)
    if kind is None:
        # A transient health (`progressing`, `suspended`, `missing`, `unknown`...) is neither a
        # delivery nor an alert: reporting it as `cd.app.degraded` rolled back a healthy start,
        # since the train listens to the CD (S22-18, #349).
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


def _argocd_kind(health: str, rollout: Any) -> InboundEventType | None:
    """What an Argo CD notification announces: delivered, degraded, rollout finished or aborted, or
    nothing, for a transient health. Only `degraded` is an alert (#349)."""
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
    session: PlatformDb,
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
        if project is not None and await _alert_reaches_memory(session, project, event):
            received_events.record(session, project, event, "signalled")
        count += 1
    return WebhookAck(accepted=True, events=count)


async def _alert_reaches_memory(session: Any, project: Project, event: InboundEvent) -> bool:
    """Hand an alert to the project's memory loop, started if it does not run (S51-02).

    `False`, and a log line, when the project's policy disables memory or the loop cannot be reached:
    Alertmanager would retry a 500 forever, and nothing it can resend changes either answer.
    """
    from ..services import policy_engine

    if not (await policy_engine(session, project.id)).memory_enabled():
        logger.info("alert not kept: memory disabled", project=project.slug, delivery=event.delivery_id)
        return False
    try:
        await signal_memory_loop(project.slug, "alert", event.model_dump(mode="json"))
    except MemoryLoopNotRunningError as exc:
        logger.warning("alert not kept: memory loop not running", project=project.slug, reason=exc.cause)
        return False
    return True


# ───────────────────────────── Jira / GitLab (S13-04) ─────────────────────────────


@router.post(
    "/jira", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="jiraWebhook"
)
async def jira_webhook(
    request: Request,
    session: PlatformDb,
    x_choregos_secret: Annotated[str, Header()] = "",
    x_atlassian_webhook_identifier: Annotated[str, Header()] = "",
) -> WebhookAck:
    """Jira Cloud does not sign its webhooks: the shared secret is the only barrier."""
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
    delivered = await _dispatch(session, events, project_of=project, tracker_of=_jira_tracker_of(session))
    return WebhookAck(accepted=True, events=delivered)


def _jira_tracker_of(session: Any) -> TrackerOf:
    """Re-reads the issue through the project's Jira connector: a webhook payload has no field names
    (`expand=names`) and is only as trustworthy as a shared secret. Best effort: a project without a
    Jira connector, or an issue that cannot be read, leaves `fields.tracker` as it was."""
    from choregos_core.domain import TRACKER_FIELD

    from ..services.tickets import project_tracker

    async def read(project: Project, key: str) -> dict[str, Any] | None:
        try:
            row, adapter = await project_tracker(session, project)
            if row is None or row.type != "jira" or adapter is None:
                return None
            found = (await adapter.fetch_item(key)).fields.get(TRACKER_FIELD)
        except Exception as failed:  # the delivery is still routed; the block waits for the next one
            logger.warning("jira.webhook.tracker_unread", key=key, error=str(failed)[:300])
            return None
        return dict(found) if isinstance(found, dict) else None

    return read


@router.post(
    "/gitlab", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED, operation_id="gitlabWebhook"
)
async def gitlab_webhook(
    request: Request,
    session: PlatformDb,
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
    org: OrgSlug, plugin: PluginName, server: PluginName, request: Request, session: PlatformDb
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
    from choregos_core.domain import TRACKER_FIELD

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
            block = item.fields.get(TRACKER_FIELD)
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
            delivered += await _dispatch(
                session,
                [event],
                project_of=project,
                tracker_of=_known(block if isinstance(block, dict) else None),
            )
        return WebhookAck(
            accepted=True, events=delivered, duplicate=bool(events) and duplicates == len(events)
        )
    raise unauthorized("no server recognised the delivery")


def _known(block: dict[str, Any] | None) -> TrackerOf:
    """What the tracker says, already re-read (a plugin server's `tracker_fetch_item`)."""

    async def read(project: Project, key: str) -> dict[str, Any] | None:
        return block

    return read
