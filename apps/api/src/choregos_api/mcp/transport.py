# SPDX-License-Identifier: Apache-2.0
"""The door's transport: Streamable HTTP, stateless, JSON responses only (ADR 0030).

`POST /mcp` and `POST /mcp/projects/{org}:{slug}`. A `GET` (the server's SSE stream) and a `DELETE`
(the end of a session) answer 405: the door has neither stream nor session, which the protocol allows.
Before any MCP exchange, the door refuses in HTTP — size, origin, version, token, rate — so that a
badly configured client reads a reason, and not a silence.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from choregos_core.mcp import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    acceptable_version,
    is_notification,
    message_defect,
    negotiate,
    ok,
    text_result,
)
from choregos_core.mcp import error as error_response
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .. import __version__
from ..audit import record
from ..config import Settings
from ..deps import Config, Db, resolve_project
from ..errors import ApiError
from ..logging import get_logger
from ..rbac import Permission
from .assistants import prompt_get, prompt_list, resolve_connector
from .caller import Caller, Refusal, identify
from .limits import RATE_LIMITER, WRITES_PER_DAY, truncate, writes_today
from .oauth import metadata
from .tools import INSTRUCTIONS, Context, ToolRefused, announced_tools

router = APIRouter(include_in_schema=False)
log = get_logger(__name__)

MAX_BODY_SIZE = 1024 * 1024


def _allowed_origins(settings: Settings) -> set[str]:
    def origin(url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    return {origin(settings.public_url), origin(settings.api_url)}


def _http_refusal(
    status: int,
    message: str,
    *,
    oauth_error: str | None = None,
    settings: Settings | None = None,
    path: str | None = None,
) -> JSONResponse:
    path = path or "/mcp"
    headers = {}
    if status in {401, 403}:
        value = 'Bearer realm="choregos"'
        if settings is not None and settings.mcp_oauth_enabled:
            # Claude reads this pointer on a 401 to find the identity provider (RFC 9728). The document
            # is that of the door called: its `resource` must be the URL the client typed.
            document = f"{settings.public_url.rstrip('/')}/.well-known/oauth-protected-resource{path}"
            value += f', resource_metadata="{document}"'
        if oauth_error:
            value += f', error="{oauth_error}"'
        headers["WWW-Authenticate"] = value
    return JSONResponse({"error": message}, status_code=status, headers=headers)


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/{path:path}")
async def resource_metadata_of_the_door(settings: Config, path: str = "mcp") -> Response:
    """The door's RFC 9728 metadata; 404 until OAuth is switched on."""
    if not settings.mcp_oauth_enabled:
        return Response(status_code=404)
    return JSONResponse(metadata(settings, "/" + path.strip("/")))


def _fault(request_id: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(error_response(request_id, code, message), status_code=status)


@router.api_route("/mcp", methods=["GET", "DELETE"])
@router.api_route("/mcp/projects/{project}", methods=["GET", "DELETE"])
@router.api_route("/mcp/assistants/{assistant}", methods=["GET", "DELETE"])
async def no_stream_or_session() -> Response:
    """The door is stateless: no SSE stream to open, no session to close."""
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp")
async def door(request: Request, session: Db, settings: Config) -> Response:
    return await _serve(request, session, settings, None)


@router.post("/mcp/assistants/{assistant}")
async def assistant_door(assistant: str, request: Request, session: Db, settings: Config) -> Response:
    """One assistant installation as its own connector (S63-11): `ask`, `get_conversation`, prompts."""
    return await _serve(request, session, settings, None, assistant)


@router.post("/mcp/projects/{project}")
async def project_door(project: str, request: Request, session: Db, settings: Config) -> Response:
    return await _serve(request, session, settings, project)


async def _serve(
    request: Request,
    session: Any,
    settings: Settings,
    requested_project: str | None,
    requested_assistant: str | None = None,
) -> Response:
    origin = request.headers.get("origin")
    if origin and origin not in _allowed_origins(settings):
        # A web page open in the person's browser does not speak in their place (DNS rebinding).
        return _http_refusal(403, f"origin refused: {origin}")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_SIZE:
        return _http_refusal(413, "message too large (1 MiB at most)")
    if not acceptable_version(request.headers.get("mcp-protocol-version")):
        return _http_refusal(400, f"unsupported protocol version: {request.headers['mcp-protocol-version']}")
    try:
        caller = await identify(session, request, settings)
    except Refusal as refusal:
        return _http_refusal(
            refusal.status, str(refusal), oauth_error=refusal.error, settings=settings, path=request.url.path
        )
    admitted, wait = RATE_LIMITER.admits(caller.key)
    if not admitted:
        return JSONResponse({"error": "rate_limited"}, status_code=429, headers={"Retry-After": str(wait)})

    project, org = caller.bound_project, caller.bound_project_org
    if requested_project is not None:
        try:
            target, target_org = await resolve_project(session, requested_project)
        except ApiError:
            return _http_refusal(404, f"project `{requested_project}` not found")
        if not caller.principal.can(Permission.PROJECT_READ, target_org, target.slug):
            return _http_refusal(404, f"project `{requested_project}` not found")
        if project is not None and project.id != target.id:
            return _http_refusal(403, f"this token is bound to project {org}:{project.slug}")
        project, org = target, target_org

    ctx = Context(
        session=session,
        caller=caller,
        project=project,
        org=org,
        console=settings.public_url.rstrip("/"),
    )
    if requested_assistant is not None and not await _pin_assistant(ctx, requested_assistant):
        return _http_refusal(404, f"assistant `{requested_assistant}` not found")

    raw = await request.body()
    if len(raw) > MAX_BODY_SIZE:
        return _http_refusal(413, "message too large (1 MiB at most)")
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _fault(None, PARSE_ERROR, "unreadable body: JSON is expected", status=400)
    defect = message_defect(message)
    if defect is not None:
        request_id = message.get("id") if isinstance(message, dict) else None
        return _fault(request_id, INVALID_REQUEST, defect, status=400)
    if is_notification(message):
        return Response(status_code=202)

    response = await _answer(ctx, message, request)
    return JSONResponse(response)


async def _pin_assistant(ctx: Context, reference: str) -> bool:
    """The door is one assistant's (S63-11); outside the audience or not ready, it does not exist."""
    ctx.assistant = await resolve_connector(ctx, reference)
    if ctx.assistant is None:
        return False
    ctx.target_org_id = ctx.assistant.installation.org_id
    return True


async def _answer(ctx: Context, message: dict[str, Any], request: Request) -> dict[str, Any]:
    request_id = message.get("id")
    method = message["method"]
    params = message.get("params") or {}
    if method == "initialize":
        return ok(
            request_id,
            {
                "protocolVersion": negotiate(params.get("protocolVersion")),
                "capabilities": {
                    "tools": {"listChanged": False},
                    **({"prompts": {"listChanged": False}} if ctx.assistant is not None else {}),
                },
                "serverInfo": {"name": "choregos", "title": "Choregos", "version": __version__},
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return ok(request_id, {})
    if method == "tools/list":
        return ok(request_id, {"tools": [tool.announcement() for tool in await announced_tools(ctx)]})
    if method == "tools/call":
        return await _call(ctx, request_id, params, request)
    if method == "prompts/list" and ctx.assistant is not None:
        return ok(request_id, {"prompts": await prompt_list(ctx)})
    if method == "prompts/get" and ctx.assistant is not None:
        rendered = await prompt_get(ctx, params)
        if isinstance(rendered, str):
            return error_response(request_id, INVALID_PARAMS, rendered)
        return ok(request_id, rendered)
    return error_response(request_id, METHOD_NOT_FOUND, f"unknown method: {method}")


async def _call(ctx: Context, request_id: Any, params: dict[str, Any], request: Request) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    tool = next((o for o in await announced_tools(ctx) if o.name == name), None)
    if tool is None or not isinstance(arguments, dict):
        # A tool outside the person's rights does not exist for them: the same answer as an unknown tool.
        return error_response(request_id, INVALID_PARAMS, f"Unknown tool: {name}")
    issue = "ok"
    try:
        if tool.write and await writes_today(ctx.session, ctx.caller.principal.user_id) >= WRITES_PER_DAY:
            # Every write counts: a work item opened, an action proposed (ADR 0030).
            raise ToolRefused(f"budget_exhausted: {WRITES_PER_DAY} writes a day through MCP")
        text, structure = await tool.execute(ctx, arguments)
        result = text_result(truncate(text), structured=structure)
    except ToolRefused as refusal:
        issue = "refused"
        result = text_result(str(refusal), is_error=True)
    await _audit(ctx, tool.name, tool.write and issue == "ok", issue, request)
    return ok(request_id, result)


async def _audit(ctx: Context, tool: str, write: bool, issue: str, request: Request) -> None:
    """Every call leaves a row (R-SOC-MCP-06) — and the day's writes are counted from them."""
    await record(
        ctx.session,
        ctx.caller.principal,
        "mcp.call",
        # A call that targets neither an organisation nor a project (`list_projects`) is filed under
        # that of the agent the client embodies: without an organisation, under RLS, the row was
        # readable by nobody — an external agent's audit vanished on PostgreSQL.
        org_id=ctx.target_org_id
        or (ctx.project.org_id if ctx.project is not None else None)
        or ctx.caller.agent_org_id,
        target_type="mcp_tool",
        target_id=tool,
        write=write,
        issue=issue,
        token=ctx.caller.key,
        agent=ctx.caller.agent,
        client=(request.headers.get("user-agent") or "")[:100] or None,
    )
    log.info("mcp.call", tool=tool, issue=issue, caller=ctx.caller.key)


__all__ = ["Caller", "router"]
