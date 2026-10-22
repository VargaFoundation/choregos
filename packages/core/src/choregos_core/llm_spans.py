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
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
from datetime import datetime
from typing import Any

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

    def __init__(self, exporter: SpanExporter | None = None) -> None:
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

    async def async_log_success_event(
        self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        self.record(kwargs, start_time, end_time, failed=False)

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
    "OPERATIONS",
    "PROVIDERS",
    "SERVICE_NAME",
    "ChoregosLlmSpans",
    "describe",
    "handler",
    "key_metadata",
    "run_trace_ids",
]
