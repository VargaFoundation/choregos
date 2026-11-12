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

Every model call it forwards is measured (ADR 0141 §4): the sections of the body it parsed, scaled to
the input tokens the gateway's answer reports — read from the JSON answer, or from the stream's events
that carry a usage —, recorded as a `context.breakdown` event (`hosted_proxy`). What the platform did not
write counts as `backend`; an answer without a usage records nothing, the total is never guessed.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
from choregos_contracts import StageInput
from choregos_core.context_breakdown import ContextMeter, Hints, prompt_tokens_of
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
#: The calls whose context is measured: the three shapes of a model call.
_MEASURED = frozenset({"chat/completions", "responses", "messages"})
#: The most of a JSON answer read for its usage; past it, the call is not measured.
_MAX_ANSWER_BYTES = 8 * 1024 * 1024


class ModelProxy:
    def __init__(
        self, stage_input: StageInput, record: Record, *, transport: Any = None, hints: Hints | None = None
    ) -> None:
        self.stage_input = stage_input
        self.record = record
        self.http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(600, connect=10))
        self.turns = 0
        self.refused_turns = 0
        #: A gateway budget refusal, word for word (S27-01).
        self.budget_refusal: str | None = None
        #: The container built the request: what the runner did not write is `backend`.
        self.context = ContextMeter(
            record,
            model=stage_input.model.litellm_model,
            source="hosted_proxy",
            hints=hints or Hints(foreign=True, mcp_servers=frozenset(stage_input.tools.mcp)),
        )

    def app(self) -> Starlette:
        return Starlette(
            routes=[Route("/{path:path}", self.handle, methods=["GET", "POST", "DELETE"])],
        )

    async def aclose(self) -> None:
        await self.context.close()
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
        chunks: AsyncIterator[bytes] = answer.aiter_bytes()
        if body is not None and path in _MEASURED:
            # The sections are counted now, from the body the proxy holds; the text is not kept.
            chunks = self._measured(
                chunks, self.context.estimate(body), answer.headers.get("content-type", "")
            )
        # Decoded bytes (`content-encoding` is not passed on), streamed as they come.
        return StreamingResponse(
            chunks,
            status_code=answer.status_code,
            headers=kept,
            background=BackgroundTask(answer.aclose),
        )

    async def _measured(
        self, chunks: AsyncIterator[bytes], estimates: Mapping[str, int], content_type: str
    ) -> AsyncIterator[bytes]:
        """The answer's bytes, passed on as they come, read on the way for the call's usage."""
        usage = _UsageReader(streamed="text/event-stream" in content_type)
        async for chunk in chunks:
            usage.feed(chunk)
            yield chunk
        await self.context.observe_estimates(estimates, usage.found())


class _UsageReader:
    """The usage of an answer, read as it passes: the whole JSON answer, or, in a stream, the last
    event whose usage says the input tokens (OpenAI's final chunk, Anthropic's `message_start`, the
    Responses API's `response.completed`). Only one line of a stream is held at a time."""

    def __init__(self, *, streamed: bool) -> None:
        self.streamed = streamed
        self.held = bytearray()
        self.usage: dict[str, Any] | None = None
        self.overflow = False

    def feed(self, chunk: bytes) -> None:
        if self.overflow:
            return
        self.held += chunk
        if not self.streamed:
            if len(self.held) > _MAX_ANSWER_BYTES:
                self.overflow, self.held = True, bytearray()
            return
        *lines, rest = bytes(self.held).split(b"\n")
        self.held = bytearray(rest)
        for line in lines:
            self._line(line)

    def found(self) -> dict[str, Any] | None:
        if self.overflow:
            return None
        if not self.streamed:
            try:
                answer = json.loads(bytes(self.held) or b"null")
            except ValueError:
                return None
            return _usage_in(answer)
        self._line(bytes(self.held))
        return self.usage

    def _line(self, line: bytes) -> None:
        line = line.strip()
        if not line.startswith(b"data:") or b"usage" not in line:
            return
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            return
        usage = _usage_in(event)
        if usage is not None:
            self.usage = usage


def _usage_in(event: Any) -> dict[str, Any] | None:
    """The usage an answer or a stream event carries, when it says the input tokens."""
    if not isinstance(event, dict):
        return None
    for holder in (event, event.get("message"), event.get("response")):
        if isinstance(holder, dict) and prompt_tokens_of(holder.get("usage")) is not None:
            return dict(holder["usage"])
    return None


def _key(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("x-api-key", "").strip()


def _error(status: int, type_: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"type": type_, "message": message}}, status_code=status)
