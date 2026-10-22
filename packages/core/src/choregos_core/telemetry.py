# SPDX-License-Identifier: Apache-2.0
"""OpenTelemetry GenAI traces: one trace per run (ADR 0018).

The GenAI semantic conventions are still in Development, so this is the ONE module that knows
their names; the orchestrator, the runner and the API call the helpers below and never spell an
attribute themselves. A test compares every name here with the pinned `opentelemetry-semantic-
conventions` package, so an upgrade that renames one fails there, not on a dashboard.

The tree of a run:

- `invoke_workflow <stage>`: the orchestrator, for one stage attempt. Its trace id and span id are
  derived from the run id, so any process that knows the run id can attach to it without being
  told anything else — the runner through `StageInput.trace_parent`, the API's tool route from the
  run id in its path.
- `invoke_agent <agent>`: the runner, for the agent's session, child of the stage span.
- `execute_tool <tool>`: the API, for each tool call through the internal route, child of the stage
  span (the API never sees the runner's span).
- `chat <model>` (or `embeddings`, `text_completion`): the gateway, for each model call made with
  the run's key, child of the stage span (`choregos_core.llm_spans`, loaded by LiteLLM).

What a span never carries: the prompt, the tool arguments, the tool result (ADR 0018 conditions).
No gate reads a span: tracing is off unless an exporter is configured, and a failure to export is
the exporter's problem, never the run's.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import NonRecordingSpan, Span, SpanContext, SpanKind, Status, StatusCode, TraceFlags

#: The semantic conventions these names come from (`opentelemetry-semantic-conventions` 0.66b0).
SEMCONV_SCHEMA_URL = "https://opentelemetry.io/schemas/1.44.0"

OPERATION_NAME = "gen_ai.operation.name"
INVOKE_WORKFLOW = "invoke_workflow"
INVOKE_AGENT = "invoke_agent"
EXECUTE_TOOL = "execute_tool"
WORKFLOW_NAME = "gen_ai.workflow.name"
AGENT_NAME = "gen_ai.agent.name"
AGENT_ID = "gen_ai.agent.id"
CONVERSATION_ID = "gen_ai.conversation.id"
PROVIDER_NAME = "gen_ai.provider.name"
REQUEST_MODEL = "gen_ai.request.model"
TOOL_NAME = "gen_ai.tool.name"
ERROR_TYPE = "error.type"
#: The gateway's model-call spans (ADR 0018 §2): `choregos_core.llm_spans` spells these too, in
#: LiteLLM's process where this module is not installed; a test keeps the two equal.
CHAT = "chat"
TEXT_COMPLETION = "text_completion"
EMBEDDINGS = "embeddings"
RESPONSE_MODEL = "gen_ai.response.model"
USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
USAGE_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"

#: The correlation keys: the structlog keys, so a log line and a span meet on the same values.
RUN_ID = "run_id"
WORK_ITEM = "work_item"
PROJECT = "project"
STAGE = "stage"
#: Ours, not the convention's: how the stage ended, as the orchestrator read it.
STAGE_OUTCOME = "choregos.stage.outcome"

#: Any of these set means an operator asked for traces; none set means tracing stays off.
_ENDPOINT_VARIABLES = ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")

_INSTRUMENTATION = "choregos"

#: The ids the next span must take, set only for the instant the stage span is started.
_forced_ids: ContextVar[tuple[int, int] | None] = ContextVar("choregos_forced_span_ids", default=None)


class _RunIds(RandomIdGenerator):
    """Random ids, except for the span started while `_forced_ids` is set: the run's own."""

    def generate_trace_id(self) -> int:
        forced = _forced_ids.get()
        return forced[0] if forced else super().generate_trace_id()

    def generate_span_id(self) -> int:
        forced = _forced_ids.get()
        return forced[1] if forced else super().generate_span_id()

    def is_trace_id_random(self) -> bool:
        return _forced_ids.get() is None


class _Tracing:
    """This process's tracer provider, or none: tracing off."""

    provider: TracerProvider | None = None


def configure_tracing(service_name: str, exporter: SpanExporter | None = None) -> bool:
    """Turns tracing on for this process, or leaves it off; says which.

    With `exporter` (tests), spans go to it synchronously. Without, an OTLP/HTTP exporter is used
    when the standard `OTEL_EXPORTER_OTLP_*ENDPOINT` variable is set, batched. Otherwise nothing
    changes — tracing stays off, and every helper below is a no-op. The global tracer provider is
    left alone: the libraries that bring their own (LiteLLM, the MCP SDK) are not ours to redirect.
    """
    processor: SpanProcessor
    if exporter is not None:
        processor = SimpleSpanProcessor(exporter)
    elif any(os.environ.get(name) for name in _ENDPOINT_VARIABLES):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        processor = BatchSpanProcessor(OTLPSpanExporter())
    else:
        return _Tracing.provider is not None
    shutdown_tracing()
    _Tracing.provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        id_generator=_RunIds(),
    )
    _Tracing.provider.add_span_processor(processor)
    return True


def shutdown_tracing() -> None:
    """Flushes and turns tracing off (process exit, end of a test)."""
    if _Tracing.provider is not None:
        _Tracing.provider.shutdown()
        _Tracing.provider = None


def _tracer() -> trace.Tracer:
    if _Tracing.provider is None:
        return trace.NoOpTracer()
    return _Tracing.provider.get_tracer(_INSTRUMENTATION, schema_url=SEMCONV_SCHEMA_URL)


def run_trace_ids(run_id: str) -> tuple[int, int]:
    """The trace id and the stage span id of a run: derived, so every process agrees on them."""
    digest = hashlib.sha256(f"choregos-run:{run_id}".encode()).digest()
    trace_id = int.from_bytes(digest[:16], "big") or 1
    span_id = int.from_bytes(digest[16:24], "big") or 1
    return trace_id, span_id


def traceparent(run_id: str) -> str:
    """The W3C `traceparent` of a run's stage span, for `StageInput.trace_parent`."""
    trace_id, span_id = run_trace_ids(run_id)
    return f"00-{trace_id:032x}-{span_id:016x}-01"


def _parse_traceparent(value: str) -> tuple[int, int] | None:
    parts = value.strip().split("-")
    if len(parts) != 4 or len(parts[1]) != 32 or len(parts[2]) != 16:
        return None
    try:
        trace_id, span_id = int(parts[1], 16), int(parts[2], 16)
    except ValueError:
        return None
    return (trace_id, span_id) if trace_id and span_id else None


def _remote_parent(trace_id: int, span_id: int) -> Context:
    parent = SpanContext(trace_id, span_id, is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED))
    return trace.set_span_in_context(NonRecordingSpan(parent))


def _correlation(
    run_id: str, project: str | None, work_item: str | None, stage: str | None
) -> dict[str, str]:
    keys = {RUN_ID: run_id, PROJECT: project, WORK_ITEM: work_item, STAGE: stage}
    return {key: value for key, value in keys.items() if value}


@contextmanager
def stage_span(
    run_id: str,
    *,
    stage: str | None = None,
    project: str | None = None,
    work_item: str | None = None,
    workflow: str | None = None,
) -> Iterator[Span]:
    """`invoke_workflow <stage>`: one stage attempt, with the run's derived ids (the trace's root)."""
    attributes: dict[str, str] = {OPERATION_NAME: INVOKE_WORKFLOW}
    if workflow:
        attributes[WORKFLOW_NAME] = workflow
    attributes.update(_correlation(run_id, project, work_item, stage))
    token = _forced_ids.set(run_trace_ids(run_id))
    try:
        span = _tracer().start_span(
            f"{INVOKE_WORKFLOW} {stage or run_id}",
            context=Context(),
            kind=SpanKind.INTERNAL,
            attributes=attributes,
        )
    finally:
        _forced_ids.reset(token)
    with trace.use_span(span, end_on_exit=True):
        yield span


@contextmanager
def agent_span(
    run_id: str,
    *,
    trace_parent: str | None,
    agent: str,
    agent_id: str | None = None,
    model: str | None = None,
    project: str | None = None,
    work_item: str | None = None,
    stage: str | None = None,
) -> Iterator[Span]:
    """`invoke_agent <agent>`: the agent's session in the runner, child of the stage span.

    The parent is `trace_parent` when the orchestrator sent one, else the run's derived span: the
    two are the same span, the second only spares a runner started by hand an orphan trace.
    """
    ids = _parse_traceparent(trace_parent) if trace_parent else None
    attributes: dict[str, str] = {
        OPERATION_NAME: INVOKE_AGENT,
        AGENT_NAME: agent,
        CONVERSATION_ID: run_id,
    }
    if agent_id:
        attributes[AGENT_ID] = agent_id
    if model:
        attributes[REQUEST_MODEL] = model
    attributes.update(_correlation(run_id, project, work_item, stage))
    with _tracer().start_as_current_span(
        f"{INVOKE_AGENT} {agent}",
        context=_remote_parent(*(ids or run_trace_ids(run_id))),
        kind=SpanKind.INTERNAL,
        attributes=attributes,
    ) as span:
        yield span


@contextmanager
def tool_span(run_id: str, tool: str) -> Iterator[Span]:
    """`execute_tool <tool>`: one tool call through the platform, child of the stage span.

    Name and duration only at the start; `record_tool_call` adds what the call learned. Never the
    arguments, never the result.
    """
    attributes = {OPERATION_NAME: EXECUTE_TOOL, TOOL_NAME: tool, CONVERSATION_ID: run_id, RUN_ID: run_id}
    with _tracer().start_as_current_span(
        f"{EXECUTE_TOOL} {tool}",
        context=_remote_parent(*run_trace_ids(run_id)),
        kind=SpanKind.INTERNAL,
        attributes=attributes,
    ) as span:
        yield span


def record_tool_call(
    *,
    status_code: int,
    provider: str | None = None,
    project: str | None = None,
    work_item: str | None = None,
    stage: str | None = None,
) -> None:
    """What the current `execute_tool` span learned: who served it, and whether it failed."""
    span = trace.get_current_span()
    if provider:
        span.set_attribute(PROVIDER_NAME, provider)
    for key, value in ((PROJECT, project), (WORK_ITEM, work_item), (STAGE, stage)):
        if value:
            span.set_attribute(key, value)
    if status_code >= 400:
        span.set_attribute(ERROR_TYPE, str(status_code))
        span.set_status(Status(StatusCode.ERROR))


def record_correlation(
    span: Span, *, project: str | None = None, work_item: str | None = None, stage: str | None = None
) -> None:
    """The correlation keys a span learned after it started (the stage span reads them from the run)."""
    for key, value in ((PROJECT, project), (WORK_ITEM, work_item), (STAGE, stage)):
        if value:
            span.set_attribute(key, value)


def record_outcome(span: Span, outcome: str, *, failed: bool) -> None:
    """How a stage or an agent session ended; a failure is the span's error status."""
    span.set_attribute(STAGE_OUTCOME, outcome)
    if failed:
        span.set_attribute(ERROR_TYPE, outcome)
        span.set_status(Status(StatusCode.ERROR))
