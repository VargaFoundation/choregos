"""The gateway's spans: each model call made with a run's key, in the run's trace (ADR 0018 §2, S40-20b)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from choregos_core import llm_spans, telemetry
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

RUN_ID = "PROJ-1-dev-1"
START = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)


def key_metadata(**extra: Any) -> dict[str, Any]:
    """What `mint_run_key` writes on a run's key."""
    return {
        "project": "proj",
        "work_item": "PROJ-1",
        "run_id": RUN_ID,
        "transition": "t-dev",
        "role": "dev",
        "attempt": 1,
        "backend": "claude-code",
        **extra,
    }


def call(
    *, key: dict[str, Any] | None = None, request: dict[str, Any] | None = None, **payload: Any
) -> dict[str, Any]:
    """LiteLLM's `kwargs` for one proxied call, as its logging callbacks receive them."""
    standard = {
        "call_type": "acompletion",
        "custom_llm_provider": "anthropic",
        "model_group": "claude-sonnet",
        "model": "claude-sonnet-5-5",
        "prompt_tokens": 1200,
        "completion_tokens": 80,
        "metadata": {"usage_object": {"prompt_tokens_details": {"cached_tokens": 1000}}},
        "messages": [{"role": "user", "content": "the prompt"}],
        "response": {"choices": [{"message": {"content": "the answer"}}]},
        **payload,
    }
    return {
        "model": "claude-sonnet",
        "messages": [{"role": "user", "content": "the prompt"}],
        "litellm_params": {
            "metadata": {
                "user_api_key_metadata": key_metadata() if key is None else key,
                **(request or {}),
            }
        },
        "standard_logging_object": standard,
    }


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def handler(exporter: InMemorySpanExporter) -> llm_spans.ChoregosLlmSpans:
    return llm_spans.ChoregosLlmSpans(exporter)


def succeed(handler: llm_spans.ChoregosLlmSpans, kwargs: dict[str, Any]) -> None:
    asyncio.run(handler.async_log_success_event(kwargs, None, START, START + timedelta(seconds=2)))


def fail(handler: llm_spans.ChoregosLlmSpans, kwargs: dict[str, Any]) -> None:
    asyncio.run(handler.async_log_failure_event(kwargs, None, START, START + timedelta(seconds=1)))


def only(exporter: InMemorySpanExporter) -> ReadableSpan:
    (span,) = exporter.get_finished_spans()
    return span


def test_the_names_are_telemetrys() -> None:
    """LiteLLM's process has no Choregos package: the copy spells every name as telemetry does."""
    for name in (
        "SEMCONV_SCHEMA_URL",
        "OPERATION_NAME",
        "CHAT",
        "TEXT_COMPLETION",
        "EMBEDDINGS",
        "CONVERSATION_ID",
        "PROVIDER_NAME",
        "REQUEST_MODEL",
        "RESPONSE_MODEL",
        "USAGE_INPUT_TOKENS",
        "USAGE_OUTPUT_TOKENS",
        "USAGE_CACHE_READ_INPUT_TOKENS",
        "ERROR_TYPE",
        "RUN_ID",
        "WORK_ITEM",
        "PROJECT",
        "STAGE",
    ):
        assert getattr(llm_spans, name) == getattr(telemetry, name), name


@pytest.mark.parametrize("run_id", ["PROJ-1-dev-1", "x", "a run id with spaces"])
def test_the_run_ids_are_telemetrys(run_id: str) -> None:
    assert llm_spans.run_trace_ids(run_id) == telemetry.run_trace_ids(run_id)


def test_a_call_is_a_child_of_the_stage_span(
    handler: llm_spans.ChoregosLlmSpans, exporter: InMemorySpanExporter
) -> None:
    """The run's trace id, the stage span as parent: the call sits in the run's tree."""
    succeed(handler, call())
    span = only(exporter)
    trace_id, stage_span_id = telemetry.run_trace_ids(RUN_ID)
    assert span.name == "chat claude-sonnet"
    assert span.kind is SpanKind.CLIENT
    assert span.context is not None and span.context.trace_id == trace_id
    assert span.parent is not None and span.parent.span_id == stage_span_id
    assert span.resource.attributes["service.name"] == "choregos-gateway"
    assert span.start_time == int(START.timestamp() * 1e9)
    assert span.end_time == int((START + timedelta(seconds=2)).timestamp() * 1e9)
    assert span.status.status_code is StatusCode.UNSET
    assert dict(span.attributes or {}) == {
        "gen_ai.operation.name": "chat",
        "gen_ai.conversation.id": RUN_ID,
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": "claude-sonnet",
        "gen_ai.response.model": "claude-sonnet-5-5",
        "gen_ai.usage.input_tokens": 1200,
        "gen_ai.usage.output_tokens": 80,
        "gen_ai.usage.cache_read.input_tokens": 1000,
        "run_id": RUN_ID,
        "project": "proj",
        "work_item": "PROJ-1",
        "stage": "dev",
    }


def test_the_call_meets_the_orchestrators_stage_span(
    handler: llm_spans.ChoregosLlmSpans, exporter: InMemorySpanExporter
) -> None:
    """Two processes, two exporters, one tree: the gateway's parent is the stage span itself."""
    stages = InMemorySpanExporter()
    telemetry.configure_tracing("orchestrator", stages)
    try:
        with telemetry.stage_span(RUN_ID, stage="dev"):
            pass
    finally:
        telemetry.shutdown_tracing()
    (stage,) = stages.get_finished_spans()
    succeed(handler, call())
    span = only(exporter)
    assert stage.context is not None and span.context is not None and span.parent is not None
    assert span.context.trace_id == stage.context.trace_id
    assert span.parent.span_id == stage.context.span_id
    assert dict(span.attributes or {})["stage"] == dict(stage.attributes or {})["stage"]


def test_a_key_that_names_no_run_has_no_span(
    handler: llm_spans.ChoregosLlmSpans, exporter: InMemorySpanExporter
) -> None:
    """The platform's own key, a session's key: no run, no trace to join."""
    succeed(handler, call(key={"project": "proj"}))
    succeed(handler, call(key={}))
    assert exporter.get_finished_spans() == ()


def test_the_requests_own_metadata_is_ignored(
    handler: llm_spans.ChoregosLlmSpans, exporter: InMemorySpanExporter
) -> None:
    """An agent holds the key, not the right to say which run it is: only the key's metadata counts."""
    forged = {"run_id": "OTHER-9-dev-1", "project": "other", "role": "deploy"}
    succeed(handler, call(key={}, request={"run_id": "OTHER-9-dev-1", "user_api_key_auth_metadata": forged}))
    assert exporter.get_finished_spans() == ()
    succeed(handler, call(request={"run_id": "OTHER-9-dev-1", "user_api_key_auth_metadata": forged}))
    attributes = dict(only(exporter).attributes or {})
    assert attributes["run_id"] == RUN_ID
    assert attributes["project"] == "proj"
    assert attributes["stage"] == "dev"


def test_a_span_never_carries_the_conversation(
    handler: llm_spans.ChoregosLlmSpans, exporter: InMemorySpanExporter
) -> None:
    succeed(handler, call())
    span = only(exporter)
    values = [str(value) for value in (span.attributes or {}).values()]
    assert not any("the prompt" in value or "the answer" in value for value in values)
    assert not any("message" in key or "prompt" in key for key in (span.attributes or {}))
    assert span.events == ()


def test_a_failed_call_is_an_error_span_without_usage(
    handler: llm_spans.ChoregosLlmSpans, exporter: InMemorySpanExporter
) -> None:
    fail(handler, call(error_information={"error_class": "RateLimitError", "error_code": "429"}))
    span = only(exporter)
    attributes = dict(span.attributes or {})
    assert span.status.status_code is StatusCode.ERROR
    assert attributes["error.type"] == "RateLimitError"
    assert not any(key.startswith("gen_ai.usage.") for key in attributes)
    assert span.parent is not None and span.parent.span_id == telemetry.run_trace_ids(RUN_ID)[1]


@pytest.mark.parametrize(
    ("litellm", "convention"),
    [("anthropic", "anthropic"), ("openai", "openai"), ("gemini", "gcp.gemini"), ("bedrock", "aws.bedrock")],
)
def test_the_provider_is_the_conventions_name(litellm: str, convention: str) -> None:
    described = llm_spans.describe(call(custom_llm_provider=litellm), failed=False)
    assert described is not None
    assert described[1]["gen_ai.provider.name"] == convention


@pytest.mark.parametrize(
    ("call_type", "operation"), [("aembedding", "embeddings"), ("atext_completion", "text_completion")]
)
def test_the_operation_follows_the_call_type(call_type: str, operation: str) -> None:
    described = llm_spans.describe(call(call_type=call_type), failed=False)
    assert described is not None
    assert described[0] == f"{operation} claude-sonnet"
    assert described[1]["gen_ai.operation.name"] == operation


def test_an_unknown_call_type_has_no_span() -> None:
    """A call the conventions have no operation for (an image, a file upload) is not guessed."""
    assert llm_spans.describe(call(call_type="image_generation"), failed=False) is None


def test_a_broken_payload_never_reaches_the_call(
    handler: llm_spans.ChoregosLlmSpans, exporter: InMemorySpanExporter
) -> None:
    broken = call()
    broken["litellm_params"] = "not a dict"
    succeed(handler, broken)
    assert exporter.get_finished_spans() == ()


def test_off_without_an_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    off = llm_spans.ChoregosLlmSpans()
    assert off.provider is None
    succeed(off, call())


def test_on_with_an_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.monitoring.svc:4318")
    on = llm_spans.ChoregosLlmSpans()
    try:
        assert on.provider is not None
    finally:
        on.shutdown()
