# SPDX-License-Identifier: Apache-2.0
"""The gateway's spans: one span per model call, in the run's own trace (ADR 0018 §2, S40-20b).

Model calls are the gateway's spans, never the runner's: the agent is a black box that talks to
LiteLLM with the run's key, and only the gateway knows what was asked, of which provider, for how
many tokens. LiteLLM loads this file as a logging callback (`callbacks: choregos_llm_spans.handler`
next to its config; the chart ships a byte-identical copy of this module, a test keeps them equal).

Why not LiteLLM's own `otel` callback: it starts a trace of its own unless the request carries a
`traceparent` header, which an agent never sends, and it records the messages unless told not to.
This one attaches each call to the run's trace with nothing from the request: the run id comes from
the **key's** metadata (`user_api_key_metadata`, written by the platform when it minted the run's
key, overwritten by the proxy on every request), and the trace id and the stage span id are derived
from it exactly as `choregos_core.telemetry.run_trace_ids` derives them. A call made with a key that
names no run (the platform's own key, an agent session's) has no span.

What a span carries: the operation (`chat`, `embeddings`, `text_completion`), the provider, the
model asked (the platform alias) and the model that answered, the token counts the provider
reported, the run's correlation keys, and an error status with `error.type` on a failed call. Never
a message, a prompt, a tool argument or a response (ADR 0018 conditions).

Stdlib and the OpenTelemetry SDK only: LiteLLM's image has the SDK and the OTLP/HTTP exporter, and
no Choregos package. Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` (or its `_TRACES_` form) is set; an
error here is logged and never reaches the call.

The same callback measures what fills an ACP CLI's context (ADR 0141 §4, S64-08b): for a call made
with the key of a run whose backend is a third-party CLI, the request it holds is split into sections
as `choregos_core.context_breakdown` splits a foreign request — a copy, kept equal by a test: a
paragraph whose digest the runner registered (`context.parts`) counts in its section, a tool named
`server__tool` after a server of the run in `tools.mcp`, everything else in `backend` — and scaled to
the `prompt_tokens` the gateway reported. The breakdown (numbers only: no text, no digest) is posted
to the API as a `context.breakdown` event with `source: gateway`, at most one per 5 seconds per run
(the first call, the largest of each window, the last). Off unless `CHOREGOS_CONTEXT_API_URL` and
`LITELLM_MASTER_KEY` are set; it never blocks the call: the registered parts are fetched once per run
(a bounded cache), each request to the API is a task of its own with a timeout, and nothing it does
raises.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
import logging
import os
import re
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, SpanKind, Status, StatusCode, TraceFlags

#: The names, as `choregos_core.telemetry` spells them (a test compares the two modules).
SEMCONV_SCHEMA_URL = "https://opentelemetry.io/schemas/1.44.0"
OPERATION_NAME = "gen_ai.operation.name"
CHAT = "chat"
TEXT_COMPLETION = "text_completion"
EMBEDDINGS = "embeddings"
CONVERSATION_ID = "gen_ai.conversation.id"
PROVIDER_NAME = "gen_ai.provider.name"
REQUEST_MODEL = "gen_ai.request.model"
RESPONSE_MODEL = "gen_ai.response.model"
USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
USAGE_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"
ERROR_TYPE = "error.type"
RUN_ID = "run_id"
WORK_ITEM = "work_item"
PROJECT = "project"
STAGE = "stage"

#: The service the gateway's spans come from, in the collector and on the dashboards.
SERVICE_NAME = "choregos-gateway"

#: LiteLLM's provider names that the conventions spell differently; any other passes as is.
PROVIDERS = {
    "gemini": "gcp.gemini",
    "vertex_ai": "gcp.vertex_ai",
    "azure": "azure.ai.openai",
    "azure_ai": "azure.ai.inference",
    "bedrock": "aws.bedrock",
    "mistral": "mistral_ai",
    "xai": "x_ai",
    "watsonx": "ibm.watsonx.ai",
}

#: LiteLLM's call types and the operation each one is; a call type not listed has no span.
OPERATIONS = {
    "completion": CHAT,
    "acompletion": CHAT,
    "anthropic_messages": CHAT,
    "responses": CHAT,
    "aresponses": CHAT,
    "text_completion": TEXT_COMPLETION,
    "atext_completion": TEXT_COMPLETION,
    "embedding": EMBEDDINGS,
    "aembedding": EMBEDDINGS,
}

_ENDPOINT_VARIABLES = ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")

_logger = logging.getLogger("choregos.llm_spans")


def run_trace_ids(run_id: str) -> tuple[int, int]:
    """The run's trace id and stage span id: `choregos_core.telemetry.run_trace_ids`, copied."""
    digest = hashlib.sha256(f"choregos-run:{run_id}".encode()).digest()
    trace_id = int.from_bytes(digest[:16], "big") or 1
    span_id = int.from_bytes(digest[16:24], "big") or 1
    return trace_id, span_id


def key_metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The calling key's metadata: the platform's, never the request's own `metadata`."""
    params = kwargs.get("litellm_params") or {}
    metadata = params.get("metadata") or {}
    value = metadata.get("user_api_key_metadata") if isinstance(metadata, dict) else None
    return value if isinstance(value, dict) else {}


def _nanoseconds(value: Any) -> int | None:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1e9)
    if isinstance(value, (int, float)) and value > 0:
        return int(value * 1e9)
    return None


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _cache_read(payload: dict[str, Any]) -> int | None:
    usage = (payload.get("metadata") or {}).get("usage_object")
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    cached = _count(details.get("cached_tokens")) if isinstance(details, dict) else None
    return cached if cached is not None else _count(usage.get("cache_read_input_tokens"))


def describe(kwargs: dict[str, Any], *, failed: bool) -> tuple[str, dict[str, Any]] | None:
    """The span's name and attributes for one call, or None when the call gets no span."""
    meta = key_metadata(kwargs)
    run_id = meta.get("run_id")
    payload = kwargs.get("standard_logging_object") or {}
    operation = OPERATIONS.get(str(payload.get("call_type") or kwargs.get("call_type") or ""))
    if not isinstance(run_id, str) or not run_id or operation is None:
        return None
    asked = str(payload.get("model_group") or kwargs.get("model") or payload.get("model") or "")
    attributes: dict[str, Any] = {OPERATION_NAME: operation, CONVERSATION_ID: run_id, RUN_ID: run_id}
    provider = str(payload.get("custom_llm_provider") or "")
    if provider:
        attributes[PROVIDER_NAME] = PROVIDERS.get(provider, provider)
    if asked:
        attributes[REQUEST_MODEL] = asked
    if payload.get("model"):
        attributes[RESPONSE_MODEL] = str(payload["model"])
    for name, value in (
        (USAGE_INPUT_TOKENS, _count(payload.get("prompt_tokens"))),
        (USAGE_OUTPUT_TOKENS, _count(payload.get("completion_tokens"))),
        (USAGE_CACHE_READ_INPUT_TOKENS, _cache_read(payload)),
    ):
        if value is not None and not failed:
            attributes[name] = value
    # The key's `role` is the stage's role, the value the orchestrator's stage span carries.
    for name, value in (
        (PROJECT, meta.get("project")),
        (WORK_ITEM, meta.get("work_item")),
        (STAGE, meta.get("role")),
    ):
        if isinstance(value, str) and value:
            attributes[name] = value
    if failed:
        error = payload.get("error_information") or {}
        attributes[ERROR_TYPE] = str(error.get("error_class") or error.get("error_code") or "error")
    return f"{operation} {asked}".strip(), attributes


# ───────────────────── what fills an ACP CLI's context (ADR 0141 §4) ─────────────────────

#: Where the API's internal routes are (`http://choregos-api:8000/api/v1/internal`).
CONTEXT_URL_VARIABLE = "CHOREGOS_CONTEXT_API_URL"
#: The backends whose context only the gateway sees: the third-party CLIs. The `prompt` and `hosted`
#: backends are measured where their requests are built (ADR 0141 §4); `code` and `remote` not at all.
ACP_CLIS = frozenset({"claude-code", "codex", "gemini-cli", "goose", "opencode", "copilot-cli"})
#: `choregos_core.context_breakdown`, copied (a test compares the two modules).
SECTIONS: tuple[str, ...] = (
    "system",
    "tools.platform",
    "tools.mcp",
    "skills",
    "knowledge",
    "memory",
    "messages",
    "backend",
)
MCP_SEPARATOR = "__"
WINDOW_S = 5.0
MIN_PARAGRAPH = 40
PARAGRAPH = re.compile(r"\n[ \t]*\n")
#: How long one request to the API may take; and how long a run whose parts could not be fetched waits
#: before they are asked again (meanwhile, its calls are all `backend`).
TIMEOUT_S = 2.0
RETRY_S = 30.0
#: The runs whose parts and throttle the process keeps: the least recently seen is dropped past it.
MAX_RUNS = 256
#: The most of an API answer read.
MAX_ANSWER_BYTES = 4 * 1024 * 1024
#: What the run's signature signs, with the gateway's master key: the API checks it the same way.
SIGNATURE_PURPOSE = "choregos-context-breakdown:"

_SYSTEM_ROLES = frozenset({"system", "developer"})
_TEXT_KEYS = ("text", "content", "output", "thinking")
_JSON_KEYS = ("arguments", "input")


def context_signature(master_key: str, run_id: str) -> str:
    """The bearer the callback speaks to the API with, for ONE run: an HMAC of the run id under the
    master key both hold. The key never travels, and a signature opens no other run."""
    return hmac.new(master_key.encode(), f"{SIGNATURE_PURPOSE}{run_id}".encode(), hashlib.sha256).hexdigest()


def estimate_tokens(text: str) -> int:
    """`choregos_core.models.estimate_tokens`, copied."""
    return max(1, (len(text) + 3) // 4)


def normalise(text: str) -> str:
    return " ".join(text.split())


def paragraph_digest(paragraph: str) -> str:
    return hashlib.sha256(normalise(paragraph).encode("utf-8")).hexdigest()


def tool_section(name: str, mcp_servers: frozenset[str]) -> str:
    segments = name.split(MCP_SEPARATOR)
    if len(segments) > 1 and any(segment in mcp_servers for segment in segments[:-1]):
        return "tools.mcp"
    return "backend"


def estimate_sections(
    body: Mapping[str, Any], registered: Mapping[str, str], mcp_servers: frozenset[str]
) -> dict[str, int]:
    """`context_breakdown.estimate_sections` of a foreign request, with what the runner registered."""
    sizes = dict.fromkeys(SECTIONS, 0)
    for tool in body.get("tools") or []:
        if isinstance(tool, dict):
            sizes[tool_section(_tool_name(tool), mcp_servers)] += estimate_tokens(json.dumps(tool))
    for text in _texts(body.get("system")), _texts(body.get("instructions")):
        _count_text(text, "backend", registered, sizes)
    messages = body.get("messages")
    if messages is None:
        messages = body.get("input")
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    for message in messages if isinstance(messages, list) else []:
        if isinstance(message, dict):
            role = message.get("role")
            section = "backend" if role in _SYSTEM_ROLES or role == "user" else "messages"
            _count_text(_message_text(message), section, registered, sizes)
    return sizes


def scale(estimates: Mapping[str, int], total: int, *, fallback: str = "backend") -> dict[str, int]:
    """`context_breakdown.scale`, copied: the estimates scaled to sum exactly to `total`."""
    sized = {section: max(0, int(estimates.get(section, 0))) for section in SECTIONS}
    scaled = dict.fromkeys(SECTIONS, 0)
    if total <= 0:
        return scaled
    weight = sum(sized.values())
    if weight == 0:
        scaled[fallback] = total
        return scaled
    remainders: list[tuple[int, int, str]] = []
    for order, section in enumerate(SECTIONS):
        share, remainder = divmod(sized[section] * total, weight)
        scaled[section] = share
        remainders.append((-remainder, order, section))
    left = total - sum(scaled.values())
    for _, _, section in sorted(remainders)[:left]:
        scaled[section] += 1
    return scaled


def _count_text(text: str, section: str, registered: Mapping[str, str], sizes: dict[str, int]) -> None:
    if not text:
        return
    if registered:
        rest: list[str] = []
        for paragraph in PARAGRAPH.split(text):
            normalised = normalise(paragraph)
            owner = registered.get(paragraph_digest(normalised)) if len(normalised) >= MIN_PARAGRAPH else None
            if owner is None:
                rest.append(paragraph)
            else:
                sizes[owner] += estimate_tokens(normalised)
        text = "\n\n".join(rest)
    if text.strip():
        sizes[section] += estimate_tokens(text)


def _tool_name(tool: Mapping[str, Any]) -> str:
    function = tool.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return str(function["name"])
    name = tool.get("name")
    return name if isinstance(name, str) else ""


def _message_text(message: Mapping[str, Any]) -> str:
    texts = [_texts(message.get(key)) for key in _TEXT_KEYS]
    texts += [_json(message.get(key)) for key in _JSON_KEYS]
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict) and isinstance(call.get("function"), dict):
            texts.append(_message_text(call["function"]))
    name = message.get("name")
    if isinstance(name, str) and message.get("type") in {"function_call", "tool_use"}:
        texts.append(name)
    return "\n\n".join(text for text in texts if text)


def _texts(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(text for text in (_texts(item) for item in value) if text)
    if isinstance(value, dict):
        return _message_text(value)
    return ""


def _json(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list) and value:
        return json.dumps(value)
    return ""


def request_of(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """The request as the callback holds it: the messages (or a Responses `input`), the system prompt
    and the tools, from the call's own fields or LiteLLM's `optional_params`."""
    optional = kwargs.get("optional_params")
    optional = optional if isinstance(optional, dict) else {}
    body: dict[str, Any] = {}
    for key in ("messages", "input", "system", "instructions", "tools"):
        value = kwargs.get(key)
        if value is None:
            value = optional.get(key)
        if value is not None:
            body[key] = value
    return body


class Registry:
    """What a run's runner registered: paragraph digests by section, and its MCP servers."""

    def __init__(
        self, registered: Mapping[str, str] | None = None, mcp_servers: frozenset[str] = frozenset()
    ) -> None:
        self.registered = dict(registered or {})
        self.mcp_servers = mcp_servers

    @classmethod
    def of(cls, answer: Any) -> Registry:
        """From the API's answer; anything it cannot read registers nothing."""
        if not isinstance(answer, dict):
            return cls()
        registered: dict[str, str] = {}
        for part in answer.get("parts") or []:
            if not isinstance(part, dict):
                continue
            section, digest = part.get("section"), part.get("sha256")
            if section in SECTIONS and section != "backend" and isinstance(digest, str):
                registered.setdefault(digest, str(section))
        servers = answer.get("mcp_servers") or []
        mcp = (
            frozenset(s for s in servers if isinstance(s, str) and s)
            if isinstance(servers, list)
            else frozenset()
        )
        return cls(registered, mcp)


class ContextApi(Protocol):
    """The API as the callback speaks to it; neither call ever raises."""

    async def parts(self, run_id: str) -> Registry | None: ...

    async def post(self, run_id: str, breakdown: dict[str, Any]) -> bool: ...


class HttpContextApi:
    """The API's internal routes, over the standard library in a worker thread, with a timeout."""

    def __init__(self, base_url: str, master_key: str, *, timeout_s: float = TIMEOUT_S) -> None:
        if urllib.parse.urlsplit(base_url).scheme not in {"http", "https"}:
            raise ValueError(f"{CONTEXT_URL_VARIABLE} must be an http(s) URL")
        self.base_url = base_url.rstrip("/")
        self.master_key = master_key
        self.timeout_s = timeout_s

    async def parts(self, run_id: str) -> Registry | None:
        answer = await self._call("GET", run_id, "context-parts", None)
        return None if answer is None else Registry.of(answer)

    async def post(self, run_id: str, breakdown: dict[str, Any]) -> bool:
        return await self._call("POST", run_id, "context-breakdown", breakdown) is not None

    async def _call(self, method: str, run_id: str, route: str, body: Any) -> Any:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._request, method, run_id, route, body), self.timeout_s + 1
            )
        except Exception as failure:
            _logger.warning(
                "choregos context breakdown: %s %s failed: %s", method, route, type(failure).__name__
            )
            return None

    def _request(self, method: str, run_id: str, route: str, body: Any) -> Any:
        url = f"{self.base_url}/runs/{urllib.parse.quote(run_id, safe='')}/{route}"
        request = urllib.request.Request(  # noqa: S310 - the scheme is checked in __init__
            url,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "authorization": f"Bearer {context_signature(self.master_key, run_id)}",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as answer:  # noqa: S310 - checked
            raw = answer.read(MAX_ANSWER_BYTES)
        return json.loads(raw or b"{}")


class Throttle:
    """`context_breakdown.Throttle`, copied: the first call, the largest of each window, the last."""

    def __init__(self, window_s: float = WINDOW_S) -> None:
        self.window_s = window_s
        self._started: float | None = None
        self._largest: dict[str, Any] | None = None
        self._last: dict[str, Any] | None = None

    def offer(self, item: dict[str, Any], now: float) -> list[dict[str, Any]]:
        if self._started is None:
            self._started = now
            return [item]
        kept: list[dict[str, Any]] = []
        if now - self._started >= self.window_s:
            if self._largest is not None:
                kept.append(self._largest)
            self._started, self._largest = now, None
        if self._largest is None or item["prompt_tokens"] > self._largest["prompt_tokens"]:
            self._largest = item
        self._last = item
        return kept

    def close(self) -> list[dict[str, Any]]:
        held = [item for item in (self._largest, self._last) if item is not None]
        unique = {id(item): item for item in held}
        self._started, self._largest, self._last = None, None, None
        return sorted(unique.values(), key=lambda item: int(item["call"]))


class _Run:
    """One run, as the callback keeps it: its calls, its throttle, its registered parts."""

    def __init__(self, window_s: float) -> None:
        self.calls = 0
        self.throttle = Throttle(window_s)
        self.registry: Registry | None = None
        self.asked_at: float | None = None
        self.lock = asyncio.Lock()
        self.flush: asyncio.TimerHandle | None = None


class ContextMeasure:
    """The breakdowns of the ACP CLIs' calls, posted to the API. `observe` returns at once: the work
    is a task, and a failure anywhere is logged, never raised."""

    def __init__(
        self,
        api: ContextApi,
        *,
        window_s: float = WINDOW_S,
        max_runs: int = MAX_RUNS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api = api
        self.window_s = window_s
        self.max_runs = max_runs
        self.clock = clock
        self.runs: OrderedDict[str, _Run] = OrderedDict()
        self.tasks: set[asyncio.Task[None]] = set()

    def observe(self, kwargs: Any) -> None:
        """One call answered: measured if its key names a CLI's run and the gateway reported its total."""
        try:
            measured = self._measured(kwargs)
            if measured is None:
                return
            run_id, model, total, body = measured
            run = self._run(run_id)
            run.calls += 1
            self._spawn(self._measure(run_id, run, run.calls, self.clock(), model, total, body))
        except Exception:
            _logger.warning("choregos context breakdown not measured", exc_info=True)

    async def drain(self) -> None:
        """Waits for the tasks in flight (a test, a shutdown)."""
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def close(self) -> None:
        """Posts what every run still holds (the last call, the open window's largest)."""
        for run_id, run in list(self.runs.items()):
            if run.flush is not None:
                run.flush.cancel()
            await self._flush(run_id, run)
        await self.drain()

    def _measured(self, kwargs: Any) -> tuple[str, str, int, dict[str, Any]] | None:
        if not isinstance(kwargs, dict):
            return None
        meta = key_metadata(kwargs)
        run_id = meta.get("run_id")
        payload = kwargs.get("standard_logging_object") or {}
        operation = OPERATIONS.get(str(payload.get("call_type") or kwargs.get("call_type") or ""))
        if (
            not isinstance(run_id, str)
            or not run_id
            or meta.get("backend") not in ACP_CLIS
            or operation != CHAT
        ):
            return None
        total = _count(payload.get("prompt_tokens"))
        if not total:
            # The total is the gateway's, never guessed: a call that reports none is not measured.
            return None
        model = str(payload.get("model_group") or kwargs.get("model") or payload.get("model") or "")
        return run_id, model, total, request_of(kwargs)

    def _run(self, run_id: str) -> _Run:
        run = self.runs.get(run_id)
        if run is None:
            run = self.runs[run_id] = _Run(self.window_s)
        self.runs.move_to_end(run_id)
        while len(self.runs) > self.max_runs:
            _, dropped = self.runs.popitem(last=False)
            if dropped.flush is not None:
                dropped.flush.cancel()
        return run

    def _spawn(self, work: Any) -> None:
        task: asyncio.Task[None] = asyncio.get_running_loop().create_task(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _registry(self, run_id: str, run: _Run) -> Registry:
        now = self.clock()
        if run.registry is None and (run.asked_at is None or now - run.asked_at >= RETRY_S):
            run.asked_at = now
            run.registry = await self.api.parts(run_id)
        return run.registry or Registry()

    async def _measure(
        self, run_id: str, run: _Run, call: int, at: float, model: str, total: int, body: dict[str, Any]
    ) -> None:
        try:
            async with run.lock:
                registry = await self._registry(run_id, run)
                estimates = estimate_sections(body, registry.registered, registry.mcp_servers)
                item = {
                    "call": call,
                    "at": datetime.now(UTC).isoformat(),
                    "model": model,
                    "prompt_tokens": total,
                    "max_input_tokens": None,
                    "source": "gateway",
                    "sections": scale(estimates, total),
                }
                for kept in run.throttle.offer(item, at):
                    await self.api.post(run_id, kept)
                self._arm(run_id, run)
        except Exception:
            _logger.warning("choregos context breakdown not posted", exc_info=True)

    def _arm(self, run_id: str, run: _Run) -> None:
        """The run's last call is posted a window after it, unless another call comes first."""
        if run.flush is not None:
            run.flush.cancel()
        loop = asyncio.get_running_loop()
        run.flush = loop.call_later(self.window_s, lambda: self._spawn(self._flush(run_id, run)))

    async def _flush(self, run_id: str, run: _Run) -> None:
        try:
            async with run.lock:
                run.flush = None
                for kept in run.throttle.close():
                    await self.api.post(run_id, kept)
        except Exception:
            _logger.warning("choregos context breakdown not posted", exc_info=True)


def context_from_environment() -> ContextMeasure | None:
    """The measure the deployment asks for: on when both the API's URL and the master key are set."""
    url, key = os.environ.get(CONTEXT_URL_VARIABLE, ""), os.environ.get("LITELLM_MASTER_KEY", "")
    if not url or not key:
        return None
    try:
        return ContextMeasure(HttpContextApi(url, key))
    except ValueError:
        _logger.warning("choregos context breakdown off: %s is not an http(s) URL", CONTEXT_URL_VARIABLE)
        return None


def _base() -> type:
    try:
        module = importlib.import_module("litellm.integrations.custom_logger")
    except ImportError:
        return object
    base: type = module.CustomLogger
    return base


_Base: Any = _base()


class ChoregosLlmSpans(_Base):  # type: ignore[misc]
    """LiteLLM's logging callback: config `litellm_settings.callbacks: [choregos_llm_spans.handler]`."""

    def __init__(self, exporter: SpanExporter | None = None, context: ContextMeasure | None = None) -> None:
        if _Base is not object:
            super().__init__()
        processor: SpanProcessor | None = None
        if exporter is not None:
            processor = SimpleSpanProcessor(exporter)
        elif any(os.environ.get(name) for name in _ENDPOINT_VARIABLES):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            processor = BatchSpanProcessor(OTLPSpanExporter())
        self.provider: TracerProvider | None = None
        if processor is not None:
            self.provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
            self.provider.add_span_processor(processor)
        #: What fills an ACP CLI's context (S64-08b): off unless the deployment names the API.
        self.context = context if context is not None else context_from_environment()

    async def async_log_success_event(
        self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.record(kwargs, start_time, end_time, failed=False)
        if self.context is not None:
            self.context.observe(kwargs)

    async def async_log_failure_event(
        self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.record(kwargs, start_time, end_time, failed=True)

    def record(self, kwargs: Any, start_time: Any, end_time: Any, *, failed: bool) -> None:
        """Emits the call's span; a failure here is logged, never raised into the call."""
        if self.provider is None or not isinstance(kwargs, dict):
            return
        try:
            described = describe(kwargs, failed=failed)
            if described is None:
                return
            name, attributes = described
            trace_id, stage_span_id = run_trace_ids(attributes[RUN_ID])
            parent = SpanContext(
                trace_id, stage_span_id, is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED)
            )
            tracer = self.provider.get_tracer("choregos", schema_url=SEMCONV_SCHEMA_URL)
            span = tracer.start_span(
                name,
                context=trace.set_span_in_context(NonRecordingSpan(parent)),
                kind=SpanKind.CLIENT,
                attributes=attributes,
                start_time=_nanoseconds(start_time),
            )
            if failed:
                span.set_status(Status(StatusCode.ERROR))
            span.end(end_time=_nanoseconds(end_time))
        except Exception:
            _logger.warning("choregos llm span not recorded", exc_info=True)

    def shutdown(self) -> None:
        """Flushes the spans still batched (process exit, end of a test)."""
        if self.provider is not None:
            self.provider.shutdown()


#: The instance LiteLLM's config names.
handler = ChoregosLlmSpans()

__all__ = [
    "ACP_CLIS",
    "OPERATIONS",
    "PROVIDERS",
    "SERVICE_NAME",
    "ChoregosLlmSpans",
    "ContextMeasure",
    "HttpContextApi",
    "Registry",
    "context_signature",
    "describe",
    "estimate_sections",
    "handler",
    "key_metadata",
    "request_of",
    "run_trace_ids",
    "scale",
]
