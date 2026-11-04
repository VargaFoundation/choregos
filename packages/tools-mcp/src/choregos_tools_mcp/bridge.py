# SPDX-License-Identifier: Apache-2.0
"""The tool bridge (ADR 0122 §1, §4, §7): a script's door to its run's tools, on the run's tool server.

The model calls a platform tool through `POST /mcp`. A script the agent wrote calls the same tools
through three routes of the same server, on the same loopback port, holding no credential:

- `GET /bridge/v1/tools` hands back the run's tool list as `getRunTools` answers it, whole
  (`inputSchema`, `outputSchema`, `x-choregos-access`): the generated client (S57-08) is built from it;
- `POST /bridge/v1/tools/{name}`, the arguments as the body, relays to `callRunTool` with the run
  token, `X-Choregos-Tool-Origin: script` and the script's `X-Choregos-Script` when it sends one, and
  hands back the API's answer unchanged: the same status, the same bytes, a problem included;
- `POST /bridge/v1/scripts` takes a script's text once (`{"sha256", "text"}`) and journals it.

The API governs each call exactly as it governs the model's (§2): the bridge adds no rule. It only
bounds what it relays: at most `max_concurrent_calls` calls of the run at once (the next one waits for
a place, it is not refused), 60 s per call (a governed write waits up to 30 s on the API's side), and
1 MiB per request body. The routes exist only when the run's stage input offers scripts
(`tools.scripts`); otherwise they answer 404, as a route that does not exist.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from choregos_contracts import ToolScripts
from choregos_core.redaction import REDACTED
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from .client import SCRIPT_HEADER, InternalClient, Relayed

PREFIX = "/bridge/v1"
#: The largest request body the bridge reads; past it, 413.
MAX_BODY_BYTES = 1024 * 1024
#: The largest script text the bridge journals (UTF-8 bytes); past it, 400.
MAX_SCRIPT_BYTES = 64 * 1024
#: How long a relayed call may take: a governed write waits up to 30 s for its outcome on the API's side.
CALL_TIMEOUT_S = 60.0
#: The journal's events: a script's text, once per digest, and each call the bridge relayed.
SCRIPT_EVENT = "tools.script"
CALL_EVENT = "tools.script.call"
PROBLEM_JSON = "application/problem+json"
#: What the journal keeps of a tool's name: the API refuses an unknown one, whatever its length.
MAX_TOOL_NAME = 200
#: A secret shorter than this is not masked: masking a short string would shred every text.
MIN_MASKED = 6
#: Where the sidecar numbers its events from. The runner numbers its own from 1 in the same run, and
#: the API keeps one event per `(run, seq)`: two counters from 1 would drop each other's events.
SIDECAR_SEQ_BASE = 1_000_000_000
#: The sidecar's numbers follow the clock, in milliseconds, within this span: a sidecar restarted
#: during its run goes on after the numbers it already used instead of starting over.
SIDECAR_SEQ_SPAN = 1_000_000_000
_SHA256 = re.compile(r"[0-9a-f]{64}")
_NOT_FOUND = {"detail": "Not Found"}


class Journal(Protocol):
    """Where the bridge writes what it relayed: the runner's `EventJournal`, or the sidecar's
    `ApiJournal`."""

    async def record(self, type_: str, payload: dict[str, Any]) -> None: ...


def problem(status: int, title: str, detail: str) -> JSONResponse:
    """An RFC 9457 problem of the bridge's own, shaped as the API's are."""
    return JSONResponse(
        {"type": "about:blank", "title": title, "status": status, "detail": detail},
        status_code=status,
        media_type=PROBLEM_JSON,
    )


class Bridge:
    """The bridge of one run: its client (the run token), its bound, its journal."""

    def __init__(
        self,
        client: InternalClient,
        scripts: ToolScripts,
        journal: Journal,
        *,
        secrets: Sequence[str] = (),
        call_timeout: float = CALL_TIMEOUT_S,
    ) -> None:
        self.client = client
        self.journal = journal
        self.max_concurrent_calls = scripts.max_concurrent_calls
        self.call_timeout = call_timeout
        self._secrets = tuple(secret for secret in secrets if len(secret) >= MIN_MASKED)
        self._places = asyncio.Semaphore(scripts.max_concurrent_calls)
        self._scripts: set[str] = set()

    async def tools(self) -> Response:
        """The run's tool list, as the API answers it."""
        try:
            return self._handed_back(await self.client.relay_tools())
        except httpx.HTTPError:
            return _unreachable()

    async def call(self, name: str, arguments: bytes, script: str | None) -> Response:
        """One call relayed to `callRunTool` once a place is free, journaled once it answered."""
        async with self._places:
            started = time.monotonic()
            try:
                # The bound is the bridge's own, whatever the transport enforces: a call that waits
                # for a connection of the client's pool waits within it too.
                async with asyncio.timeout(self.call_timeout):
                    relayed = await self.client.relay_tool_call(
                        name, arguments or b"{}", script=script, within_s=self.call_timeout
                    )
                response, outcome = self._handed_back(relayed), _outcome(relayed)
            except (TimeoutError, httpx.TimeoutException):
                response = problem(
                    504,
                    "Tool call timed out",
                    f"the internal API did not answer within {self.call_timeout:g} s; the call may still "
                    "complete, and the same write called again is the same governed action",
                )
                outcome = 504
            except httpx.HTTPError:
                response, outcome = _unreachable(), 502
            duration_ms = round((time.monotonic() - started) * 1000)
        await self._record(
            CALL_EVENT,
            {
                "tool": name[:MAX_TOOL_NAME],
                "status_code": outcome,
                "duration_ms": duration_ms,
                "response_bytes": len(response.body),
                "script_sha256": script,
            },
        )
        return response

    async def script(self, body: bytes) -> Response:
        """A script's text, journaled once per digest; its digest must be the SHA-256 of its text."""
        expected = 'expected {"sha256": <64 lowercase hexadecimal characters>, "text": <the script>}'
        try:
            document = json.loads(body)
        except ValueError:
            return problem(400, "Script refused", f"the body is not JSON: {expected}")
        text = document.get("text") if isinstance(document, dict) else None
        digest = document.get("sha256") if isinstance(document, dict) else None
        if not isinstance(text, str) or not isinstance(digest, str):
            return problem(400, "Script refused", expected)
        try:
            raw = text.encode("utf-8")
        except UnicodeEncodeError:
            return problem(400, "Script refused", "the text is not valid Unicode")
        if len(raw) > MAX_SCRIPT_BYTES:
            return problem(
                400, "Script refused", f"the script is {len(raw)} bytes long, {MAX_SCRIPT_BYTES} at most"
            )
        if _SHA256.fullmatch(digest) is None or hashlib.sha256(raw).hexdigest() != digest:
            return problem(
                400, "Script refused", "`sha256` is not the SHA-256 of `text` in UTF-8, in lowercase hex"
            )
        answer = {"sha256": digest, "bytes": len(raw)}
        if digest in self._scripts:
            return JSONResponse({**answer, "journaled": False})
        self._scripts.add(digest)
        await self._record(SCRIPT_EVENT, {**answer, "text": self._masked(text)})
        return JSONResponse({**answer, "journaled": True}, status_code=201)

    def _handed_back(self, relayed: Relayed) -> Response:
        """The API's answer as it came: status, bytes, media type, `Retry-After`."""
        headers = {"content-type": relayed.media_type}
        if relayed.retry_after is not None:
            headers["retry-after"] = relayed.retry_after
        content = relayed.content
        for secret in self._secrets:
            # Never the case: the run token is the runner's, and no answer carries it. Were it ever
            # to, the bridge would hand it to the agent, which ADR 0115 keeps it from.
            content = content.replace(secret.encode(), REDACTED.encode())
        return Response(content, status_code=relayed.status_code, headers=headers)

    def _masked(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text

    async def _record(self, type_: str, payload: dict[str, Any]) -> None:
        # A lost journal line never fails a call: the API has the call's own event either way.
        with contextlib.suppress(Exception):
            await self.journal.record(type_, payload)


def _outcome(relayed: Relayed) -> int:
    """The call's outcome as the script reads it: the answer's `status_code` on a 200 (`202` for a
    proposal, a provider's 4xx), the HTTP status of a problem (`429` past the cap, `404`)."""
    if relayed.status_code == 200:
        with contextlib.suppress(ValueError):
            answer = json.loads(relayed.content)
            code = answer.get("status_code") if isinstance(answer, dict) else None
            if isinstance(code, int) and not isinstance(code, bool):
                return code
    return relayed.status_code


def _unreachable() -> JSONResponse:
    return problem(502, "Internal API unreachable", "the tool bridge could not reach the internal API")


class StageInputBridge:
    """The bridge of a server that is not told its run's input: the Tekton sidecar.

    The sidecar starts before the runner and holds the run's token, as the runner does: it reads the
    run's stage input from the API (`GET /internal/runs/{id}/input`) on the bridge's first request, once,
    and serves the bridge only if the input offers scripts. Read lazily, as the MCP server already
    reads the input's memories and outputs: the sidecar's readiness never waits on the API, and a run
    whose agent never calls the bridge pays nothing. Nothing in the sandbox can turn the bridge on:
    the decision comes from the API, with the run token. An input that cannot be read is not cached:
    the next request asks again."""

    def __init__(self, client: InternalClient, journal: Journal, *, secrets: Sequence[str] = ()) -> None:
        self.client = client
        self.journal = journal
        self._secrets = tuple(secrets)
        self._lock = asyncio.Lock()
        self._resolved = False
        self._bridge: Bridge | None = None

    async def __call__(self) -> Bridge | None:
        if not self._resolved:
            async with self._lock:
                if not self._resolved:
                    scripts = (await self.client.fetch_input()).tools.scripts
                    if scripts is not None:
                        self._bridge = Bridge(self.client, scripts, self.journal, secrets=self._secrets)
                    self._resolved = True
        return self._bridge


class ApiJournal:
    """The sidecar's journal: each event posted to the run's events at once, through the internal
    API (`POST /internal/runs/{id}/events`), numbered from `SIDECAR_SEQ_BASE` so that it never takes
    a number the runner uses. The run's events are ordered by number, so the sidecar's come after the
    runner's in the run's journal; each carries its own time (`ts`)."""

    def __init__(self, client: InternalClient, *, clock: Callable[[], float] = time.time) -> None:
        self.client = client
        self._clock = clock
        self._last = SIDECAR_SEQ_BASE

    def _next(self) -> int:
        stamp = SIDECAR_SEQ_BASE + int(self._clock() * 1000) % SIDECAR_SEQ_SPAN
        self._last = max(self._last + 1, stamp)
        return self._last

    async def record(self, type_: str, payload: dict[str, Any]) -> None:
        event = {
            "seq": self._next(),
            "type": type_,
            "ts": datetime.fromtimestamp(self._clock(), UTC).isoformat(),
            "payload": payload,
        }
        with contextlib.suppress(Exception):  # a lost journal never fails a call
            await self.client.post_events([event])


BridgeSource = Callable[[], Awaitable[Bridge | None]]


def mount(app: FastAPI, source: BridgeSource) -> None:
    """The bridge's routes on the tool server's app. `source` says, at each request, whether the run
    is offered scripts: no bridge, a 404 as from a route that does not exist."""

    async def offered() -> Bridge | Response:
        try:
            bridge = await source()
        except Exception:
            return problem(
                503,
                "Tool bridge unavailable",
                "the run's input could not be read, so whether this run is offered tool scripts is not "
                "known yet; try again",
            )
        return JSONResponse(_NOT_FOUND, status_code=404) if bridge is None else bridge

    @app.get(f"{PREFIX}/tools")
    async def bridge_tools() -> Response:
        bridge = await offered()
        return bridge if isinstance(bridge, Response) else await bridge.tools()

    @app.post(PREFIX + "/tools/{name}")
    async def bridge_call(name: str, request: Request) -> Response:
        bridge = await offered()
        if isinstance(bridge, Response):
            return bridge
        script = request.headers.get(SCRIPT_HEADER)
        if script is not None and _SHA256.fullmatch(script) is None:
            return problem(
                400,
                "Script digest refused",
                f"{SCRIPT_HEADER} must be a SHA-256 in 64 lowercase hexadecimal characters",
            )
        body = await _bounded_body(request)
        if body is None:
            return _too_large()
        if name in {".", ".."}:
            return problem(404, "Tool not found", f"Tool `{name}` does not exist")
        return await bridge.call(name, body, script)

    @app.post(f"{PREFIX}/scripts")
    async def bridge_script(request: Request) -> Response:
        bridge = await offered()
        if isinstance(bridge, Response):
            return bridge
        body = await _bounded_body(request)
        return _too_large() if body is None else await bridge.script(body)


async def _bounded_body(request: Request) -> bytes | None:
    """The request's body, or `None` past `MAX_BODY_BYTES`: read no further than the bound."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


def _too_large() -> JSONResponse:
    return problem(413, "Request too large", f"the tool bridge reads {MAX_BODY_BYTES} bytes at most")


__all__ = [
    "CALL_EVENT",
    "CALL_TIMEOUT_S",
    "MAX_BODY_BYTES",
    "MAX_SCRIPT_BYTES",
    "PREFIX",
    "SCRIPT_EVENT",
    "SIDECAR_SEQ_BASE",
    "ApiJournal",
    "Bridge",
    "BridgeSource",
    "Journal",
    "StageInputBridge",
    "mount",
]
