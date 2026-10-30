# SPDX-License-Identifier: Apache-2.0
"""HTTP triggers (S51-01, ADR 0113): the door another system calls, its token, and firing it by hand.

`POST /webhooks/triggers/{org}/{project}/{name}` takes the caller's JSON with the trigger's token
(`Authorization: Bearer`, `X-Choregos-Token`, or `?token=` for a sender that can only set a URL), maps
it onto a work item and starts it in the trigger's workflow or the one the project's routing picks.
The project routes list the declared triggers, mint a token (shown once) and fire a trigger with a
payload typed by a person — the same mapping, initiated by that person.

A refused call (a missing key, fields the workflow refuses, an external tracker) answers its 4xx
problem document AND commits its `received_events` row: the route returns the problem instead of
raising it, so the session commits.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Header, Path, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from ..audit import record
from ..config import get_settings
from ..db.models import Organization, Project
from ..db.org_aliases import organisation_by_slug_or_alias
from ..deps import Db, DbPlateforme, ProjectCtx
from ..errors import PROBLEM_JSON, not_found, unauthorized
from ..rbac import SYSTEM, Permission
from ..schemas import TriggerAck, TriggerDto, TriggerTokenDto
from ..security import body_digest
from ..services import triggers
from ..services.definitions import project_config
from ..services.initiator import initiator, of_principal

router = APIRouter(tags=["work-items"])

Name = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
Slug = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
#: The most a trigger reads: a ticket, an alert, a form — not a file.
MAX_BODY = 256 * 1024


def trigger_url(org_slug: str, project_slug: str, name: str) -> str:
    api = get_settings().api_url.rstrip("/")
    return f"{api}/api/v1/webhooks/triggers/{org_slug}/{project_slug}/{name}"


def _ack(firing: triggers.Firing) -> TriggerAck | JSONResponse:
    if firing.refusal is not None:
        return JSONResponse(
            firing.refusal.to_problem(),
            status_code=firing.refusal.status_code,
            media_type=PROBLEM_JSON,
        )
    item = firing.item
    return TriggerAck(
        accepted=True,
        duplicate=firing.outcome == "duplicate",
        outcome=None if firing.outcome == "duplicate" else firing.outcome,
        work_item_id=item.id if item is not None else None,
        work_item_key=item.tracker_key if item is not None else None,
        workflow=firing.workflow,
    )


# ───────────────────────────── The project's side ─────────────────────────────


@router.get("/projects/{id}/triggers", response_model=list[TriggerDto], operation_id="listTriggers")
async def list_triggers(ctx: ProjectCtx, session: Db) -> list[TriggerDto]:
    """The triggers the project's configuration declares; any member reads them, never a token."""
    declared = project_config(ctx.project).triggers
    rows = {r.name: r for r in await triggers.token_rows(session, ctx.project)}
    return [
        TriggerDto(
            name=t.name,
            url=trigger_url(ctx.org_slug, ctx.slug, t.name),
            workflow=t.workflow,
            title=t.title,
            key=t.key,
            labels=list(t.labels),
            fields=dict(t.fields),
            when=dict(t.when),
            auth=t.auth,
            has_token=t.name in rows,
            token_created_at=rows[t.name].created_at if t.name in rows else None,
        )
        for t in declared
    ]


@router.post(
    "/projects/{id}/triggers/{name}/token",
    response_model=TriggerTokenDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="rotateTriggerToken",
)
async def rotate_trigger_token(ctx: ProjectCtx, name: Name, session: Db) -> TriggerTokenDto:
    """Mints the trigger's token, shown once; the previous one stops working. Whoever may change the
    project's connectors may: a token is how another system writes into the project."""
    ctx.require(Permission.CONNECTOR_WRITE)
    if triggers.declared(ctx.project, name) is None:
        raise not_found("Trigger", name)
    token = await triggers.rotate_token(session, ctx.project, name, ctx.principal.email)
    await record(
        session,
        ctx.principal,
        "trigger.token.rotate",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
        trigger=name,
    )
    return TriggerTokenDto(
        name=name,
        url=trigger_url(ctx.org_slug, ctx.slug, name),
        token=token,
        signing_secret=triggers.signing_secret(triggers.token_digest(token)),
    )


@router.post(
    "/projects/{id}/triggers/{name}/fire",
    response_model=TriggerAck,
    operation_id="fireTrigger",
)
async def fire_trigger(
    ctx: ProjectCtx,
    name: Name,
    session: Db,
    payload: Annotated[dict[str, Any], Body()],
    idempotency_key: Annotated[str, Header()] = "",
) -> TriggerAck | JSONResponse:
    """Fires the trigger by hand with a payload a person typed: the same mapping, the same workflow
    or routing, initiated by that person. Whoever may start a work item may."""
    ctx.require(Permission.ITEM_CONTROL)
    trigger = triggers.declared(ctx.project, name)
    if trigger is None:
        raise not_found("Trigger", name)
    firing = await triggers.fire(
        session,
        ctx.project,
        trigger,
        payload,
        delivery_id=idempotency_key[:200] or _digest_of(payload),
        principal=ctx.principal,
        initiated_by=of_principal(ctx.principal, "api"),
        event_type="trigger.manual",
    )
    return _ack(firing)


def _digest_of(payload: Any) -> str:
    return body_digest(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())


# ───────────────────────────── The door another system calls ─────────────────────────────


async def _project(session: Any, org: str, project: str) -> Project | None:
    # A renamed organisation keeps its trigger URLs: an old slug is an alias (ADR 0090).
    owner: Organization | None = await organisation_by_slug_or_alias(session, org)
    if owner is None:
        return None
    found: Project | None = (
        await session.execute(select(Project).where(Project.org_id == owner.id, Project.slug == project))
    ).scalar_one_or_none()
    return found


def _token(authorization: str, x_choregos_token: str, request: Request) -> str:
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return x_choregos_token or request.query_params.get("token", "")


@router.post(
    "/webhooks/triggers/{org}/{project}/{name}",
    tags=["webhooks"],
    response_model=TriggerAck,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="triggerWebhook",
)
async def trigger_webhook(
    org: Slug,
    project: Slug,
    name: Name,
    request: Request,
    session: DbPlateforme,
    authorization: Annotated[str, Header()] = "",
    x_choregos_token: Annotated[str, Header()] = "",
    idempotency_key: Annotated[str, Header()] = "",
) -> TriggerAck | JSONResponse:
    """Another system opens a work item. 404: no such trigger, or none with a token; 401: a wrong
    token, or a wrong or missing `X-Choregos-Signature` for a trigger with `auth: signature`
    (S51-04); 413: a body over 256 KiB; 422: not a JSON object, or a work item the workflow refuses;
    409: the project's tracker is external. A replayed delivery (`Idempotency-Key`, else the body's
    digest) answers `duplicate` and starts nothing."""
    target = await _project(session, org, project)
    trigger = triggers.declared(target, name) if target is not None else None
    row = await triggers.token_row(session, target, name) if target is not None else None
    if target is None or trigger is None or row is None:
        raise not_found("Trigger", f"{org}/{project}/{name}")
    body = await request.body()
    if trigger.auth == "signature":
        # Only the signature: a bearer token would let a sender skip what the trigger asks for.
        secret = triggers.signing_secret(row.token_sha256)
        if not triggers.verify_signature(secret, body, request.headers.get("x-choregos-signature", "")):
            raise unauthorized("invalid or missing X-Choregos-Signature")
    elif not await triggers.verify_token(
        session, target, name, _token(authorization, x_choregos_token, request)
    ):
        raise unauthorized("invalid trigger token")
    if len(body) > MAX_BODY:
        return JSONResponse(
            {"type": "about:blank", "title": "Payload too large", "status": 413},
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            media_type=PROBLEM_JSON,
        )
    try:
        payload: Any = json.loads(body or b"{}")
    except ValueError:
        payload = None  # refused by `fire` as not a JSON object, and recorded as such
    firing = await triggers.fire(
        session,
        target,
        trigger,
        payload,
        delivery_id=idempotency_key[:200] or body_digest(body),
        principal=SYSTEM,
        initiated_by=initiator("trigger", name),
        event_type="trigger.http",
    )
    return _ack(firing)
