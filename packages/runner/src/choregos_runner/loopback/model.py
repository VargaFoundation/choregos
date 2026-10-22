# SPDX-License-Identifier: Apache-2.0
"""The model a hosted container reaches on `127.0.0.1:4001` (ADR 0080).

The container's SDKs point at it with the loopback key (`OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`). The
proxy forwards chat completions, responses, messages and embeddings to the run's gateway with the
run's key, which never enters the container, and with the run's model, whatever the container named.
Every call is a turn: past `max_turns` the proxy refuses the call (400, which no SDK retries) instead
of forwarding it.

It is also the Foundry project an agent written for Foundry finds in `FOUNDRY_PROJECT_ENDPOINT`
(S41-19): `/api/projects/<project>/openai/v1/<path>` is the same as `/v1/<path>`, and conversations
(`conversations[/<id>[/items[/<id>]]]`) are forwarded too, as stored state rather than turns. And it
is the managed identity `DefaultAzureCredential` finds in `IDENTITY_ENDPOINT`: `GET /msi/token` with
`X-IDENTITY-HEADER` answers the loopback key as an access token, for any resource, which only this
listener accepts.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
from choregos_contracts import StageInput
from choregos_core.hosted import IDENTITY_HEADER, IDENTITY_PATH, LOOPBACK_KEY
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..budget import gateway_budget_refusal
from . import Record

#: What the container may ask of the model, under `/v1/`.
PATHS = frozenset({"chat/completions", "responses", "messages", "embeddings", "models"})
#: A Foundry project's OpenAI-compatible root, before the same paths (S41-19).
_FOUNDRY_ROOT = re.compile(r"^api/projects/[A-Za-z0-9._-]+/openai/")
#: The conversations API: stored items, not model calls, so not turns.
_CONVERSATIONS = re.compile(r"^conversations(/[A-Za-z0-9_-]+(/items(/[A-Za-z0-9_-]+)?)?)?$")
#: How long the identity's token says it lasts; the SDKs fetch another one before it ends.
_TOKEN_LIFETIME_S = 3600
#: The request headers that reach the gateway; the container's own credentials never do.
_FORWARDED = ("content-type", "accept", "anthropic-version", "anthropic-beta", "openai-beta")


class ModelProxy:
    def __init__(self, stage_input: StageInput, record: Record, *, transport: Any = None) -> None:
        self.stage_input = stage_input
        self.record = record
        self.http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(600, connect=10))
        self.turns = 0
        self.refused_turns = 0
        #: A gateway budget refusal, word for word (S27-01).
        self.budget_refusal: str | None = None

    def app(self) -> Starlette:
        return Starlette(
            routes=[Route("/{path:path}", self.handle, methods=["GET", "POST", "DELETE"])],
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def handle(self, request: Request) -> Response:
        raw = str(request.path_params["path"]).strip("/")
        if raw == IDENTITY_PATH.strip("/"):
            return await self._identity(request)
        path = _FOUNDRY_ROOT.sub("", raw).removeprefix("v1/")
        stored = _CONVERSATIONS.match(path) is not None
        if path not in PATHS and not stored:
            return _error(404, "not_found", f"`/{path}` is not served on the loopback model")
        if request.method == "DELETE" and not stored:
            return _error(405, "method_not_allowed", f"`DELETE /{path}` is not served")
        if _key(request) != LOOPBACK_KEY:
            return _error(401, "auth_error", "the loopback model accepts the loopback key only")
        if request.method != "POST" or stored:
            return await self._forward(request, path, None, content=await request.body())
        try:
            body = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            return _error(400, "invalid_request", "the body is not JSON")
        if not isinstance(body, dict):
            return _error(400, "invalid_request", "the body is not a JSON object")
        limit = self.stage_input.budget.max_turns
        if self.turns >= limit:
            self.refused_turns += 1
            await self.record("hosted.model_refused", {"path": path, "reason": "max_turns", "limit": limit})
            # 400, not 429: every SDK retries a 429 with backoff, and each retry is refused again.
            return _error(400, "max_turns", f"the step has used its {limit} model calls (max_turns)")
        self.turns += 1
        body["model"] = self.stage_input.model.litellm_model
        return await self._forward(request, path, body)

    async def _identity(self, request: Request) -> Response:
        """App Service's managed identity protocol, which `ManagedIdentityCredential` speaks when
        `IDENTITY_ENDPOINT` and `IDENTITY_HEADER` are set."""
        if request.method != "GET":
            return _error(405, "method_not_allowed", "the identity endpoint answers GET only")
        if request.headers.get("x-identity-header") != IDENTITY_HEADER:
            return _error(401, "auth_error", "the identity endpoint wants its `X-IDENTITY-HEADER`")
        resource = request.query_params.get("resource") or request.query_params.get("scope") or ""
        await self.record("hosted.identity_token", {"resource": resource[:200]})
        return JSONResponse(
            {
                "access_token": LOOPBACK_KEY,
                "expires_on": str(int(time.time()) + _TOKEN_LIFETIME_S),
                "resource": resource,
                "token_type": "Bearer",
            }
        )

    async def _forward(
        self, request: Request, path: str, body: dict[str, Any] | None, *, content: bytes | None = None
    ) -> Response:
        model = self.stage_input.model
        headers = {name: request.headers[name] for name in _FORWARDED if name in request.headers}
        if self.stage_input.gateway_key:
            headers["authorization"] = f"Bearer {self.stage_input.gateway_key}"
            if path == "messages":
                headers["x-api-key"] = self.stage_input.gateway_key
        upstream = self.http.build_request(
            request.method,
            f"{model.base_url.rstrip('/')}/{path}",
            params=str(request.query_params),
            headers=headers,
            content=json.dumps(body).encode() if body is not None else content or None,
        )
        try:
            answer = await self.http.send(upstream, stream=True)
        except httpx.HTTPError as exc:
            await self.record(
                "hosted.model_call", {"turn": self.turns, "path": path, "error": str(exc)[:300]}
            )
            return _error(502, "gateway_unreachable", "the platform's model gateway did not answer")
        await self.record(
            "hosted.model_call", {"turn": self.turns, "path": path, "status": answer.status_code}
        )
        kept = {k: v for k, v in answer.headers.items() if k.lower() in ("content-type", "cache-control")}
        if answer.status_code >= 400:
            text = (await answer.aread()).decode(errors="replace")
            await answer.aclose()
            self.budget_refusal = self.budget_refusal or gateway_budget_refusal([text])
            return Response(text, status_code=answer.status_code, headers=kept)
        # Decoded bytes (`content-encoding` is not passed on), streamed as they come.
        return StreamingResponse(
            answer.aiter_bytes(),
            status_code=answer.status_code,
            headers=kept,
            background=BackgroundTask(answer.aclose),
        )


def _key(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("x-api-key", "").strip()


def _error(status: int, type_: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"type": type_, "message": message}}, status_code=status)
