# SPDX-License-Identifier: Apache-2.0
"""The card and the JSON-RPC endpoint of the A2A door, A2A 0.3.0 as ADR 0062 pins it (ADR 0078).

`GET /a2a/orgs/{org}/.well-known/agent-card.json` and `POST /a2a/orgs/{org}`. The task IS the work
item: `message/send` opens one through `creer_un_ticket`, under an id derived from the
organisation, the client and the `messageId` (ADR 0008), and `tasks/get` reads it back.
`message/stream` and `tasks/resubscribe` follow it over server-sent events: an event of the work
item only wakes the stream up, the task is read again from the database each time. No state lives
in this layer. Push notifications are `push.py`'s (ADR 0078 §5).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import re
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from choregos_adapters.storage import ObjectStoreError
from choregos_contracts import (
    A2A_MAX_FILE_BYTES_CEILING,
    A2A_MAX_FILE_BYTES_DEFAULT,
    ARTIFACT_NAME_PATTERN,
    ARTIFACT_TYPES,
    A2aConfig,
    Control,
    type_of_filename,
)
from choregos_core.mcp import ERREUR_DE_SYNTAXE as PARSE_ERROR
from choregos_core.mcp import ERREUR_INTERNE as INTERNAL_ERROR
from choregos_core.mcp import (
    METHODE_INCONNUE,
    PARAMETRES_INVALIDES,
    REQUETE_INVALIDE,
    defaut_du_message,
    erreur,
    est_notification,
    ok,
)
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette import EventSourceResponse

from .. import __version__
from ..audit import record
from ..config import Settings
from ..db.models import ArtifactRecord, HumanRequest, Organization, Project, WorkflowDef, WorkItem
from ..db.session import session_scope
from ..deps import Config, Db
from ..errors import ApiError
from ..events import get_bus
from ..logging import get_logger
from ..mcp.appelant import SCOPES_A2A, Appelant, Refus, identifier
from ..mcp.garde_fous import ECRITURES_PAR_JOUR, LIMITEUR, ecritures_du_jour
from ..mcp.oauth import emetteur
from ..mcp.transport import TAILLE_MAX_DU_CORPS, _origines_admises, _refus_http
from ..object_store import object_store
from ..rbac import Permission
from ..schemas import WorkItemCreate
from ..services import creer_un_ticket, workflow_du_ticket, workflow_model
from ..services.artifacts import (
    ArtifactCorruptedError,
    StoredInput,
    artifacts_of,
    filename_of,
    put_input_file,
    read_content,
    record_input_file,
    sent_by_caller,
)
from ..services.tickets import start_work_item
from ..temporal import InterpreterNotRunningError, deliver_control

router = APIRouter(include_in_schema=False)
log = get_logger(__name__)

#: The A2A version the platform speaks, the one ADR 0062 pins for outbound calls.
PROTOCOL_VERSION = "0.3.0"
#: A message is a request for work, not a document drop: a few parts, a bounded text.
MAX_PARTS = 20
MAX_TEXT = 100_000
MAX_MESSAGE_ID = 200
#: A finished task carries its artefacts' content while it stays small: 256 KiB each, 1 MiB in all.
MAX_ARTIFACT_INLINE = 256 * 1024
MAX_ARTIFACTS_INLINE = 1024 * 1024
#: A2A's own JSON-RPC errors (0.3.0 §8.2).
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
PUSH_NOT_SUPPORTED = -32003
UNSUPPORTED_OPERATION = -32004
CONTENT_TYPE_NOT_SUPPORTED = -32005
#: Implementation-defined: the person's daily write budget, shared with the MCP door.
BUDGET_EXHAUSTED = -32050
#: Implementation-defined: too many streams open for one token.
TOO_MANY_STREAMS = -32051
#: A stream holds a connection: a few per token, an hour at most, then the caller resubscribes.
MAX_STREAMS = 5
STREAM_LIFETIME = 3600.0
#: The task is read again on an event of the work item, and at least this often if none comes.
STREAM_POLL = 30.0
#: Events come in bursts (a state change, a comment, a request): one read for the burst.
STREAM_SETTLE = 0.2
STREAM_PING = 15
#: The task states after which nothing changes: the stream says `final` and closes.
TERMINAL = frozenset({"completed", "failed", "canceled"})
_STREAMS: Counter[str] = Counter()
#: The stream's clock for its lifetime; a test moves it instead of waiting an hour.
_clock = time.monotonic
#: Derives a task id from (org, client, messageId): a resent message finds its work item.
TASK_NAMESPACE = uuid5(NAMESPACE_URL, "https://choregos.dev/a2a/task")
_ARTIFACT_NAME = re.compile(ARTIFACT_NAME_PATTERN)


def _encoded(size: int) -> int:
    """The length of `size` bytes in base64, as a file part carries them."""
    return 4 * ((size + 2) // 3)


#: The largest body the door reads before it knows the organisation: a message and, in base64,
#: the most file bytes a project may take (ADR 0078 §6). The organisation's own cap comes next.
MAX_BODY = TAILLE_MAX_DU_CORPS + _encoded(A2A_MAX_FILE_BYTES_CEILING)


def file_limit(project: Project) -> int:
    """What a project's skills take in file parts, per part and per message (`a2a.max_file_bytes`)."""
    try:
        return A2aConfig.model_validate((project.config or {}).get("a2a") or {}).max_file_bytes
    except ValidationError:
        return A2A_MAX_FILE_BYTES_DEFAULT


@dataclass(frozen=True, slots=True)
class SentFile:
    """A file part of a message, decoded and checked, not stored yet."""

    name: str
    media_type: str
    type: str
    data: bytes


class Fault(Exception):  # noqa: N818 - a JSON-RPC error answered to the caller, not a crash
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass(slots=True)
class Skill:
    id: str
    project: Project
    workflow: str
    description: str
    input_modes: list[str]


@dataclass(slots=True)
class Door:
    session: AsyncSession
    caller: Appelant
    org: Organization
    console: str
    #: `None` for a push notification, which reads the task without a request.
    request: Request | None
    #: Set by a call that writes: the daily budget counts it.
    wrote: bool = False


# ───────────────────────── who and what ─────────────────────────


async def _open(
    request: Request, session: AsyncSession, settings: Settings, org_slug: str
) -> Door | Response:
    """The door's HTTP refusals, before any A2A exchange: origin, token, rate, organisation."""
    origin = request.headers.get("origin")
    if origin and origin not in _origines_admises(settings):
        return _refus_http(403, f"origin refused: {origin}")
    try:
        caller = await identifier(session, request, settings, scopes=SCOPES_A2A, door="A2A")
    except Refus as refus:
        return _refus_http(
            refus.statut, str(refus), erreur_oauth=refus.erreur, settings=settings, chemin=request.url.path
        )
    allowed, wait = LIMITEUR.admet("a2a:" + caller.cle)
    if not allowed:
        return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": str(wait)})
    org = (
        await session.execute(select(Organization).where(Organization.slug == org_slug))
    ).scalar_one_or_none()
    principal = caller.principal
    visible = org is not None and (
        org_slug in principal.org_roles or any(k.startswith(f"{org_slug}/") for k in principal.project_roles)
    )
    if org is None or not visible:
        return _refus_http(404, f"organisation `{org_slug}` not found")
    if caller.projet_lie is not None and caller.projet_lie.org_id != org.id:
        return _refus_http(
            403, f"this token is bound to project {caller.org_du_projet_lie}:{caller.projet_lie.slug}"
        )
    return Door(session, caller, org, settings.public_url.rstrip("/"), request)


async def _projects(door: Door) -> list[Project]:
    if door.caller.projet_lie is not None:
        projects = [door.caller.projet_lie]
    else:
        projects = list(
            (
                await door.session.execute(
                    select(Project).where(Project.org_id == door.org.id).order_by(Project.slug)
                )
            ).scalars()
        )
    return [p for p in projects if door.caller.principal.can(Permission.PROJECT_READ, door.org.slug, p.slug)]


async def skills(door: Door) -> list[Skill]:
    """The opted-in workflows (`a2a:`) of the projects the caller may read: nothing else is served."""
    found: list[Skill] = []
    for project in await _projects(door):
        rows = (
            await door.session.execute(
                select(WorkflowDef)
                .where(WorkflowDef.project_id == project.id, WorkflowDef.is_active.is_(True))
                .order_by(WorkflowDef.name)
            )
        ).scalars()
        for row in rows:
            opt_in = workflow_model(row).a2a
            if opt_in is None:
                continue
            found.append(
                Skill(
                    id=f"{project.slug}/{opt_in.skill}",
                    project=project,
                    workflow=row.name,
                    description=opt_in.description,
                    input_modes=list(opt_in.input_modes),
                )
            )
    return found


def _card(door: Door, settings: Settings, served: list[Skill]) -> dict[str, Any]:
    schemes: dict[str, Any] = {"bearer": {"type": "http", "scheme": "bearer"}}
    if settings.mcp_oauth_enabled:
        schemes["oidc"] = {
            "type": "openIdConnect",
            "openIdConnectUrl": f"{emetteur(settings)}/.well-known/openid-configuration",
        }
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": f"Choregos — {door.org.name}",
        "description": (
            f"The governed workflows of {door.org.name} that opted in as A2A skills. A task is a work "
            "item: it may wait for a person, who decides in the Choregos console, never over A2A."
        ),
        "url": f"{door.console}/a2a/orgs/{door.org.slug}",
        "preferredTransport": "JSONRPC",
        "version": __version__,
        "provider": {"organization": "Choregos", "url": door.console},
        "capabilities": {"streaming": True, "pushNotifications": True, "stateTransitionHistory": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "securitySchemes": schemes,
        "security": [{name: ["a2a"] if name == "oidc" else []} for name in schemes],
        "skills": [
            {
                "id": skill.id,
                "name": skill.id,
                "description": skill.description,
                "tags": [skill.project.slug, skill.workflow],
                "inputModes": skill.input_modes,
                "outputModes": ["text/plain", "application/json"],
            }
            for skill in served
        ],
    }


# ───────────────────────── routes ─────────────────────────


@router.get("/a2a/orgs/{org}/.well-known/agent-card.json")
async def agent_card(org: str, request: Request, session: Db, settings: Config) -> Response:
    """The organisation's card. Its skills depend on who asks, so it is read with a token."""
    door = await _open(request, session, settings, org)
    if isinstance(door, Response):
        return door
    return JSONResponse(_card(door, settings, await skills(door)))


@router.post("/a2a/orgs/{org}")
async def endpoint(org: str, request: Request, session: Db, settings: Config) -> Response:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY:
        return _refus_http(413, f"message too large ({MAX_BODY} bytes at most)")
    door = await _open(request, session, settings, org)
    if isinstance(door, Response):
        return door
    cap = await _body_cap(door)
    if length and length.isdigit() and int(length) > cap:
        return _refus_http(413, f"message too large ({cap} bytes at most for this organisation)")
    body = await request.body()
    if len(body) > cap:
        return _refus_http(413, f"message too large ({cap} bytes at most for this organisation)")
    try:
        message = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(erreur(None, PARSE_ERROR, "unreadable body: JSON is expected"), status_code=400)
    fault = defaut_du_message(message)
    if fault is not None:
        ident = message.get("id") if isinstance(message, dict) else None
        return JSONResponse(erreur(ident, REQUETE_INVALIDE, fault), status_code=400)
    if est_notification(message):
        return Response(status_code=202)
    ident, method = message.get("id"), message["method"]
    params = message.get("params") or {}
    outcome = "ok"
    try:
        if not isinstance(params, dict):
            raise Fault(PARAMETRES_INVALIDES, "`params` is an object")
        if method in {"message/stream", "tasks/resubscribe"}:
            stream = await _open_stream(door, settings, ident, method, params)
            await _audit(door, method, outcome)
            await session.commit()
            return stream
        answer = ok(ident, await _dispatch(door, method, params))
    except Fault as refused:
        outcome = "refus"
        answer = erreur(ident, refused.code, str(refused), refused.data)
    await _audit(door, method, outcome)
    return JSONResponse(answer)


async def _body_cap(door: Door) -> int:
    """A message, and the file bytes the most generous project of the organisation takes, in base64."""
    configs = (await door.session.execute(select(Project).where(Project.org_id == door.org.id))).scalars()
    largest = max((file_limit(project) for project in configs), default=0)
    return TAILLE_MAX_DU_CORPS + _encoded(largest)


async def _dispatch(door: Door, method: str, params: dict[str, Any]) -> Any:
    if method == "message/send":
        return await _send(door, params)
    if method == "tasks/get":
        item, project = await _task(door, params.get("id"))
        return await task_of(door, item, project)
    if method == "tasks/cancel":
        return await _cancel(door, params)
    if method.startswith("tasks/pushNotificationConfig/"):
        from .push import configure

        return await configure(door, method, params)
    raise Fault(METHODE_INCONNUE, f"unknown method: {method}")


async def _audit(door: Door, method: str, outcome: str) -> None:
    """Every call leaves a line; the writes of the day are counted from it, with the MCP door's."""
    await record(
        door.session,
        door.caller.principal,
        "a2a.call",
        org_id=door.org.id,
        target_type="a2a_method",
        target_id=method[:100],
        ecriture=door.wrote,
        issue=outcome,
        jeton=door.caller.cle,
        agent=door.caller.agent,
        client=(door.request.headers.get("user-agent") or "")[:100] or None if door.request else None,
    )
    log.info("a2a.call", method=method[:100], issue=outcome, appelant=door.caller.cle)


# ───────────────────────── message/send ─────────────────────────


def _pick_skill(served: list[Skill], metadata: dict[str, Any]) -> Skill:
    nested = metadata.get("choregos")
    wanted = metadata.get("skill") or (nested.get("skill") if isinstance(nested, dict) else None)
    if wanted is None:
        if len(served) == 1:
            return served[0]
        raise Fault(PARAMETRES_INVALIDES, "name the skill in `message.metadata.skill` (see the agent card)")
    matches = [s for s in served if wanted in {s.id, s.id.split("/", 1)[1]}]
    if len(matches) != 1:
        # A skill outside the caller's rights does not exist for them: the same answer as an unknown one.
        raise Fault(
            PARAMETRES_INVALIDES, f"unknown skill: {wanted}" if not matches else f"ambiguous skill: {wanted}"
        )
    return matches[0]


def _type_of(media_type: str, name: str) -> str:
    """The artefact type of a sent file: the one its media type names, else its extension's."""
    named = next((t for t, entry in ARTIFACT_TYPES.items() if entry.media_type == media_type), None)
    return named or type_of_filename(name)


def _read_file(part: dict[str, Any], skill: Skill, index: int, limit: int) -> SentFile:
    """A file part (A2A 0.3.0 `FilePart`): its bytes in base64, a media type the skill takes."""
    file = part.get("file")
    if not isinstance(file, dict):
        raise Fault(PARAMETRES_INVALIDES, "a file part carries `file`, with its content in `bytes`")
    if "bytes" not in file:
        # Fetching a URL the caller chose would be a request made on a stranger's behalf.
        raise Fault(PARAMETRES_INVALIDES, "a file part by `uri` is not fetched: send its content in `bytes`")
    media_type = file.get("mimeType")
    if not isinstance(media_type, str) or not media_type:
        raise Fault(PARAMETRES_INVALIDES, "a file part names its `mimeType`")
    if media_type not in skill.input_modes:
        raise Fault(CONTENT_TYPE_NOT_SUPPORTED, f"skill {skill.id} does not take {media_type}")
    name = file.get("name") or f"attachment-{index}"
    if not isinstance(name, str) or not _ARTIFACT_NAME.fullmatch(name):
        raise Fault(
            PARAMETRES_INVALIDES,
            f"file name refused: {str(name)[:130]!r} (letters, digits, `_`, `.` and `-`, 128 at most)",
        )
    raw = file["bytes"]
    if not isinstance(raw, str) or len(raw) > _encoded(limit):
        raise Fault(PARAMETRES_INVALIDES, f"file `{name}` too large: {limit} bytes at most for this project")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as unreadable:
        raise Fault(PARAMETRES_INVALIDES, f"file `{name}`: `bytes` is not base64") from unreadable
    if len(data) > limit:
        raise Fault(PARAMETRES_INVALIDES, f"file `{name}` too large: {limit} bytes at most for this project")
    return SentFile(name=name, media_type=media_type, type=_type_of(media_type, name), data=data)


def _read_parts(parts: Any, skill: Skill) -> tuple[str, dict[str, Any], list[SentFile]]:
    """The text parts become the work item's body, one data part its fields, each file an input
    artefact (ADR 0078 §6), within the project's limit for one part and for all of them."""
    if not isinstance(parts, list) or not parts:
        raise Fault(PARAMETRES_INVALIDES, "`message.parts` is a non-empty list")
    if len(parts) > MAX_PARTS:
        raise Fault(PARAMETRES_INVALIDES, f"too many parts: {MAX_PARTS} at most")
    limit = file_limit(skill.project)
    texts: list[str] = []
    fields: dict[str, Any] = {}
    files: list[SentFile] = []
    for part in parts:
        kind = part.get("kind") if isinstance(part, dict) else None
        if kind == "text" and isinstance(part.get("text"), str):
            if "text/plain" not in skill.input_modes:
                raise Fault(CONTENT_TYPE_NOT_SUPPORTED, f"skill {skill.id} does not take text/plain")
            texts.append(part["text"])
        elif kind == "data" and isinstance(part.get("data"), dict):
            if "application/json" not in skill.input_modes:
                raise Fault(CONTENT_TYPE_NOT_SUPPORTED, f"skill {skill.id} does not take application/json")
            if fields:
                raise Fault(PARAMETRES_INVALIDES, "one data part at most: it carries the work item's fields")
            fields = part["data"]
        elif kind == "file":
            sent = _read_file(part, skill, len(files) + 1, limit)
            if any(f.name == sent.name for f in files):
                raise Fault(PARAMETRES_INVALIDES, f"two files named `{sent.name}`: a name is an artefact's")
            files.append(sent)
            if sum(len(f.data) for f in files) > limit:
                raise Fault(
                    PARAMETRES_INVALIDES,
                    f"the files of a message: {limit} bytes at most together for this project",
                )
        else:
            raise Fault(PARAMETRES_INVALIDES, "a part is `text`, `data` or `file`")
    text = "\n\n".join(texts)
    if len(text) > MAX_TEXT:
        raise Fault(PARAMETRES_INVALIDES, f"message too long: {MAX_TEXT} characters of text at most")
    return text, fields, files


def task_id_of(org_id: str, client: str, message_id: str) -> str:
    """The work item a message opens, the same however many times it is sent (ADR 0008)."""
    return str(uuid5(TASK_NAMESPACE, f"{org_id}|{client}|{message_id}"))


async def _send(door: Door, params: dict[str, Any]) -> dict[str, Any]:
    message = params.get("message")
    if not isinstance(message, dict):
        raise Fault(PARAMETRES_INVALIDES, "`message` is missing")
    configuration = params.get("configuration")
    if isinstance(configuration, dict) and configuration.get("pushNotificationConfig"):
        # Its secret would have nowhere to be shown: `set` is the one answer that carries it.
        raise Fault(
            PARAMETRES_INVALIDES,
            "register push notifications with tasks/pushNotificationConfig/set once the task exists",
        )
    message_id = message.get("messageId")
    if not isinstance(message_id, str) or not message_id or len(message_id) > MAX_MESSAGE_ID:
        raise Fault(
            PARAMETRES_INVALIDES, f"`message.messageId` is a string of 1 to {MAX_MESSAGE_ID} characters"
        )
    if message.get("taskId"):
        # Answering a waiting task would be a decision: a re-authenticated person takes it (ADR 0030 §5).
        item, project = await _task(door, message["taskId"])
        raise Fault(
            UNSUPPORTED_OPERATION,
            "a work item is not continued over A2A: a decision is taken by a person in the Choregos "
            f"console, at {_console_url(door, project, item)}",
            {"decision_url": _console_url(door, project, item)},
        )
    raw = message.get("metadata")
    metadata: dict[str, Any] = raw if isinstance(raw, dict) else {}
    task_id = task_id_of(door.org.id, door.caller.cle, message_id)
    if await door.session.get(WorkItem, task_id) is not None:
        # Sent again: the work item it opened, nothing written.
        return await task_of(door, *await _task(door, task_id))
    skill = _pick_skill(await skills(door), metadata)
    project, principal = skill.project, door.caller.principal
    if not principal.can(Permission.ITEM_CONTROL, door.org.slug, project.slug):
        raise Fault(PARAMETRES_INVALIDES, f"you cannot open work items in {door.org.slug}:{project.slug}")
    text, fields, files = _read_parts(message.get("parts"), skill)
    if await ecritures_du_jour(door.session, principal.user_id) >= ECRITURES_PAR_JOUR:
        raise Fault(
            BUDGET_EXHAUSTED, f"budget_exhausted: {ECRITURES_PAR_JOUR} writes a day through MCP and A2A"
        )
    title = metadata.get("title") if isinstance(metadata.get("title"), str) else ""
    title = (title or next((line for line in text.splitlines() if line.strip()), "") or skill.id).strip()[
        :200
    ]
    body = text + _files_note(files) if text else _files_note(files).lstrip()
    try:
        # With files, the interpreter starts once they are recorded: the first step finds its inputs.
        demande = WorkItemCreate(
            title=title, body=body, workflow=skill.workflow, fields=fields, start=not files
        )
    except ValidationError as refused:
        raise Fault(PARAMETRES_INVALIDES, f"invalid message: {refused.errors()[0]['msg']}") from refused
    stored = await _put_files(door, task_id, files)
    try:
        item = await creer_un_ticket(door.session, principal, project, demande, canal="a2a", item_id=task_id)
    except ApiError as refused:
        raise Fault(PARAMETRES_INVALIDES, refused.detail or refused.title) from refused
    for one in stored:
        row = await record_input_file(door.session, org_id=door.org.id, item=item, stored=one)
        await record(
            door.session,
            principal,
            "workitem.input_file",
            org_id=door.org.id,
            target_type="work_item",
            target_id=item.id,
            name=row.name,
            version=row.version,
            digest=row.digest,
            size=row.size,
            canal="a2a",
        )
    if stored:
        await start_work_item(project, item)
    door.wrote = True
    return await task_of(door, item, project)


def _files_note(files: list[SentFile]) -> str:
    """What the work item's body says of the files: a reader of the console sees what came with it."""
    if not files:
        return ""
    lines = "\n".join(f"- `{f.name}` ({f.media_type}, {len(f.data)} bytes)" for f in files)
    return f"\n\nFiles sent with the message, kept as input artefacts:\n{lines}"


async def _put_files(door: Door, task_id: str, files: list[SentFile]) -> list[StoredInput]:
    """The files in the object store before the work item exists: a failure leaves nothing behind."""
    store = object_store()
    stored: list[StoredInput] = []
    for sent in files:
        try:
            stored.append(
                await put_input_file(
                    store,
                    org_id=door.org.id,
                    work_item_id=task_id,
                    name=sent.name,
                    type_=sent.type,
                    data=sent.data,
                )
            )
        except ObjectStoreError as failed:
            log.error("a2a.input_file_not_stored", name=sent.name, error=str(failed))
            raise Fault(
                INTERNAL_ERROR, f"file `{sent.name}` could not be stored; nothing was created"
            ) from failed
    return stored


# ───────────────────────── the task, a view of the work item ─────────────────────────


async def _task(door: Door, reference: Any) -> tuple[WorkItem, Project]:
    """A work item of this organisation the caller may read; any other is not found."""
    item = await door.session.get(WorkItem, reference) if isinstance(reference, str) and reference else None
    project = await door.session.get(Project, item.project_id) if item is not None else None
    bound = door.caller.projet_lie
    if (
        item is None
        or project is None
        or project.org_id != door.org.id
        or (bound is not None and bound.id != project.id)
        or not door.caller.principal.can(Permission.PROJECT_READ, door.org.slug, project.slug)
    ):
        raise Fault(TASK_NOT_FOUND, f"task not found: {reference}")
    return item, project


def _console_url(door: Door, project: Project, item: WorkItem) -> str:
    return f"{door.console}/p/{project.slug}/items/{item.id}"


def _status_message(item: WorkItem, state: str, parts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "kind": "message",
        "role": "agent",
        "messageId": f"{item.id}:{state}",
        "taskId": item.id,
        "contextId": item.id,
        "parts": parts,
    }


async def task_of(door: Door, item: WorkItem, project: Project) -> dict[str, Any]:
    """The work item read as an A2A task. States map one way; nothing here can move it."""
    link = _console_url(door, project, item)
    workflow = workflow_model(await workflow_du_ticket(door.session, item))
    declared = workflow.states.get(item.state)
    terminal = declared is not None and declared.terminal
    waiting = (
        await door.session.execute(
            select(HumanRequest)
            .where(HumanRequest.work_item_id == item.id, HumanRequest.decided_at.is_(None))
            .order_by(HumanRequest.requested_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    artifacts: list[dict[str, Any]] = []
    parts: list[dict[str, Any]]
    if item.failure:
        state = "failed"
        parts = [{"kind": "text", "text": f"The work item failed in state `{item.state}`. See {link}"}]
    elif terminal:
        state = "completed"
        parts = [{"kind": "text", "text": f"The work item reached `{item.state}`. See {link}"}]
        artifacts = await _artifacts(door, item, link)
    elif item.closed_at is not None:
        state = "canceled"
        parts = [{"kind": "text", "text": f"The work item was closed in state `{item.state}`. See {link}"}]
    elif waiting is not None:
        state = "input-required"
        question = waiting.payload.get("question") or waiting.payload.get("summary") or ""
        parts = [
            {
                "kind": "text",
                "text": (
                    f"Waiting for a person's {waiting.kind}. It is decided in the Choregos console, "
                    f"never over A2A: {link}" + (f"\n\n{str(question)[:1000]}" if question else "")
                ),
            },
            {"kind": "data", "data": {"decision_url": link, "kind": waiting.kind, "request_id": waiting.id}},
        ]
    else:
        state = "working"
        parts = [{"kind": "text", "text": f"In state `{item.state}`. Follow it at {link}"}]
    return {
        "kind": "task",
        "id": item.id,
        "contextId": item.id,
        "status": {"state": state, "message": _status_message(item, state, parts)},
        "artifacts": artifacts,
        "metadata": {
            "choregos": {
                "work_item": item.id,
                "key": item.tracker_key,
                "project": f"{door.org.slug}:{project.slug}",
                "state": item.state,
                "console_url": link,
            }
        },
    }


async def _artifacts(door: Door, item: WorkItem, link: str) -> list[dict[str, Any]]:
    """The latest revision of each artefact, its content in parts while the task stays small.

    The content is read through `read_content`, which checks the digest: bytes that no longer match
    are withheld, as on the console's download. A larger artefact, or one past the task's budget, is
    described and read in the console. A file the caller sent is not sent back to it."""
    latest: dict[str, ArtifactRecord] = {}
    for row in await artifacts_of(door.session, item.id):
        latest[row.name] = row
    latest = {name: row for name, row in latest.items() if not sent_by_caller(row)}
    budget = MAX_ARTIFACTS_INLINE
    served: list[dict[str, Any]] = []
    for row in latest.values():
        description: dict[str, Any] = {
            "name": row.name,
            "type": row.type,
            "media_type": row.media_type,
            "version": row.version,
            "digest": row.digest,
            "size": row.size,
            "console_url": link,
        }
        part: dict[str, Any] | None = None
        if row.size > MAX_ARTIFACT_INLINE:
            description["content"] = (
                f"not in the task: over {MAX_ARTIFACT_INLINE} bytes, read it in the console"
            )
        elif row.size > budget:
            description["content"] = (
                f"not in the task: its artefacts' content is capped at {MAX_ARTIFACTS_INLINE} bytes, "
                "read it in the console"
            )
        else:
            try:
                part = _content_part(row, await read_content(object_store(), row))
                budget -= row.size
            except ArtifactCorruptedError:
                description["content"] = "withheld: its bytes no longer match its digest"
            except ObjectStoreError:
                description["content"] = "unavailable: the object store did not serve it, try again"
        served.append(
            {
                "artifactId": f"{row.name}@{row.version}",
                "name": row.name,
                "parts": [part or {"kind": "data", "data": description}],
                "metadata": {"choregos": description},
            }
        )
    return served


def _content_part(row: ArtifactRecord, data: bytes) -> dict[str, Any]:
    """Markdown is text, a JSON object is data; anything else travels as a file, its bytes in base64."""
    if row.storage_key is None and row.type == "markdown" and isinstance(row.content, str):
        return {"kind": "text", "text": row.content}
    if row.storage_key is None and isinstance(row.content, dict):
        return {"kind": "data", "data": row.content}
    return {
        "kind": "file",
        "file": {
            "name": filename_of(row),
            "mimeType": row.media_type,
            "bytes": base64.b64encode(data).decode("ascii"),
        },
    }


async def _cancel(door: Door, params: dict[str, Any]) -> dict[str, Any]:
    item, project = await _task(door, params.get("id"))
    principal = door.caller.principal
    if not principal.can(Permission.ITEM_CONTROL, door.org.slug, project.slug):
        raise Fault(TASK_NOT_CANCELABLE, "you cannot stop work items in this project")
    if (await task_of(door, item, project))["status"]["state"] in {"completed", "failed", "canceled"}:
        raise Fault(TASK_NOT_CANCELABLE, "the task is already over")
    try:
        await deliver_control(
            project.slug,
            item.tracker_key,
            Control(action="stop", reason="canceled over A2A", by=principal.email),
        )
    except InterpreterNotRunningError as gone:
        raise Fault(TASK_NOT_CANCELABLE, "the work item's interpreter is not running") from gone
    door.wrote = True
    await record(
        door.session,
        principal,
        "workitem.stop",
        org_id=project.org_id,
        target_type="work_item",
        target_id=item.id,
        reason="canceled over A2A",
    )
    return await task_of(door, item, project)


# ───────────────────────── message/stream, tasks/resubscribe ─────────────────────────


async def _open_stream(
    door: Door, settings: Settings, ident: Any, method: str, params: dict[str, Any]
) -> Response:
    """The refusals come as a plain JSON-RPC answer; past them, the task's events as SSE."""
    if _STREAMS[door.caller.cle] >= MAX_STREAMS:
        raise Fault(TOO_MANY_STREAMS, f"too many streams: {MAX_STREAMS} open at most for one token")
    if method == "message/stream":
        first = await _send(door, params)
        item, project = await _task(door, first["id"])
    else:
        item, project = await _task(door, params.get("id"))
        first = await task_of(door, item, project)
    events = _follow(door, settings, ident, first, project.slug, item.tracker_key)
    return EventSourceResponse(events, ping=STREAM_PING)


def _frame(ident: Any, result: dict[str, Any]) -> dict[str, str]:
    return {"data": json.dumps(ok(ident, result))}


def _status_update(task: dict[str, Any], final: bool) -> dict[str, Any]:
    return {
        "kind": "status-update",
        "taskId": task["id"],
        "contextId": task["contextId"],
        "status": task["status"],
        "final": final,
        "metadata": task["metadata"],
    }


def _mark(task: dict[str, Any]) -> tuple[str, str, Any]:
    """What makes a new status worth sending: the A2A state, the workflow's state, the request."""
    data = [p.get("data") for p in task["status"]["message"]["parts"] if p.get("kind") == "data"]
    request = data[0].get("request_id") if data and isinstance(data[0], dict) else None
    return task["status"]["state"], task["metadata"]["choregos"]["state"], request


async def _follow(
    door: Door, settings: Settings, ident: Any, first: dict[str, Any], project_slug: str, tracker_key: str
) -> AsyncIterator[dict[str, str]]:
    """The task, then a status each time it changes, its artefacts and a `final` status at the end.

    An input-required status is not final: the decision is taken in the console, not by this
    caller, and the task goes on. A token revoked or a right lost ends the stream at the next read.
    """
    key = door.caller.cle
    _STREAMS[key] += 1
    try:
        yield _frame(ident, first)
        task, seen = first, _mark(first)
        deadline = _clock() + STREAM_LIFETIME
        with get_bus().listen(f"project:{project_slug}") as queue:
            while task["status"]["state"] not in TERMINAL:
                left = deadline - _clock()
                if left <= 0:
                    return
                await _wake(queue, tracker_key, min(STREAM_POLL, left))
                try:
                    task = await _reread(door, settings, task["id"])
                except Fault as lost:
                    yield {"data": json.dumps(erreur(ident, lost.code, str(lost)))}
                    return
                if _mark(task) == seen:
                    continue
                seen = _mark(task)
                final = task["status"]["state"] in TERMINAL
                for artifact in task["artifacts"] if final else []:
                    yield _frame(
                        ident,
                        {
                            "kind": "artifact-update",
                            "taskId": task["id"],
                            "contextId": task["contextId"],
                            "artifact": artifact,
                            "append": False,
                            "lastChunk": True,
                        },
                    )
                yield _frame(ident, _status_update(task, final))
        if first["status"]["state"] in TERMINAL:
            yield _frame(ident, _status_update(first, True))
    finally:
        _STREAMS[key] -= 1
        if _STREAMS[key] <= 0:
            del _STREAMS[key]


async def _wake(queue: asyncio.Queue[Any], tracker_key: str, wait: float) -> None:
    """Back on an event of this work item, settled for its burst, or after `wait` seconds anyway."""
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(wait):
            while (await queue.get()).subject != tracker_key:
                pass
        await asyncio.sleep(STREAM_SETTLE)
    while not queue.empty():
        queue.get_nowait()


async def _reread(door: Door, settings: Settings, task_id: str) -> dict[str, Any]:
    """The task from the database, for a caller identified again: the stream holds no rights."""
    assert door.request is not None, "a stream is always opened by a request"
    async with session_scope() as session:
        try:
            caller = await identifier(session, door.request, settings, scopes=SCOPES_A2A, door="A2A")
        except Refus as refus:
            raise Fault(TASK_NOT_FOUND, f"the stream's credential no longer opens the door: {refus}") from (
                refus
            )
        again = replace(door, session=session, caller=caller)
        return await task_of(again, *await _task(again, task_id))
