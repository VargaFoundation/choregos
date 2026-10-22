"""GenAI traces: the names, the run's derived ids, the tree of a run (ADR 0018, S40-20)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from choregos_core import telemetry
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.semconv import schemas
from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as gen_ai
from opentelemetry.semconv.attributes import error_attributes
from opentelemetry.trace import StatusCode


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    assert telemetry.configure_tracing("test", exporter)
    yield exporter
    telemetry.shutdown_tracing()


def test_the_names_are_the_pinned_conventions() -> None:
    """The one module that spells the names spells them as the pinned semconv package does."""
    assert list(schemas.Schemas)[-1].value == telemetry.SEMCONV_SCHEMA_URL
    assert telemetry.OPERATION_NAME == gen_ai.GEN_AI_OPERATION_NAME
    assert gen_ai.GenAiOperationNameValues.INVOKE_WORKFLOW.value == telemetry.INVOKE_WORKFLOW
    assert gen_ai.GenAiOperationNameValues.INVOKE_AGENT.value == telemetry.INVOKE_AGENT
    assert gen_ai.GenAiOperationNameValues.EXECUTE_TOOL.value == telemetry.EXECUTE_TOOL
    assert telemetry.WORKFLOW_NAME == gen_ai.GEN_AI_WORKFLOW_NAME
    assert telemetry.AGENT_NAME == gen_ai.GEN_AI_AGENT_NAME
    assert telemetry.AGENT_ID == gen_ai.GEN_AI_AGENT_ID
    assert telemetry.CONVERSATION_ID == gen_ai.GEN_AI_CONVERSATION_ID
    assert telemetry.PROVIDER_NAME == gen_ai.GEN_AI_PROVIDER_NAME
    assert telemetry.REQUEST_MODEL == gen_ai.GEN_AI_REQUEST_MODEL
    assert telemetry.TOOL_NAME == gen_ai.GEN_AI_TOOL_NAME
    assert telemetry.ERROR_TYPE == error_attributes.ERROR_TYPE


def test_off_without_an_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """No endpoint, no exporter: nothing is recorded and the helpers still work."""
    for name in ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    assert not telemetry.configure_tracing("test")
    with telemetry.stage_span("run-1", stage="dev") as span:
        assert not span.is_recording()


def test_on_with_an_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.monitoring.svc:4318")
    try:
        assert telemetry.configure_tracing("test")
    finally:
        telemetry.shutdown_tracing()


def test_the_run_ids_are_derived_and_the_traceparent_names_them() -> None:
    trace_id, span_id = telemetry.run_trace_ids("PROJ-1-dev-1")
    assert (trace_id, span_id) == telemetry.run_trace_ids("PROJ-1-dev-1")
    assert telemetry.run_trace_ids("PROJ-1-dev-2")[0] != trace_id
    assert telemetry.traceparent("PROJ-1-dev-1") == f"00-{trace_id:032x}-{span_id:016x}-01"


def test_a_run_reads_as_one_tree(spans: InMemorySpanExporter) -> None:
    """Stage → agent → tool: one trace id, each span the child of the right one, keys on each."""
    run_id = "PROJ-1-dev-1"
    with telemetry.stage_span(run_id, stage="dev", project="proj", work_item="PROJ-1") as stage:
        telemetry.record_outcome(stage, "finished", failed=False)
    with telemetry.agent_span(
        run_id,
        trace_parent=telemetry.traceparent(run_id),
        agent="claude-code",
        model="claude-sonnet",
        project="proj",
        work_item="PROJ-1",
        stage="dev",
    ):
        pass
    with telemetry.tool_span(run_id, "search_docs"):
        telemetry.record_tool_call(status_code=200, provider="greffon:docs", project="proj")

    by_name = {span.name: span for span in spans.get_finished_spans()}
    assert set(by_name) == {"invoke_workflow dev", "invoke_agent claude-code", "execute_tool search_docs"}
    trace_id, span_id = telemetry.run_trace_ids(run_id)
    root = by_name["invoke_workflow dev"]
    assert root.context is not None
    assert (root.context.trace_id, root.context.span_id) == (trace_id, span_id)
    assert root.parent is None
    for child in ("invoke_agent claude-code", "execute_tool search_docs"):
        span = by_name[child]
        assert span.context is not None and span.context.trace_id == trace_id
        assert span.parent is not None and span.parent.span_id == span_id
    assert dict(root.attributes or {}) == {
        "gen_ai.operation.name": "invoke_workflow",
        "run_id": run_id,
        "project": "proj",
        "work_item": "PROJ-1",
        "stage": "dev",
        "choregos.stage.outcome": "finished",
    }
    agent = dict(by_name["invoke_agent claude-code"].attributes or {})
    assert agent["gen_ai.operation.name"] == "invoke_agent"
    assert agent["gen_ai.conversation.id"] == run_id
    assert agent["gen_ai.request.model"] == "claude-sonnet"
    tool = by_name["execute_tool search_docs"]
    assert dict(tool.attributes or {}) == {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "search_docs",
        "gen_ai.conversation.id": run_id,
        "gen_ai.provider.name": "greffon:docs",
        "run_id": run_id,
        "project": "proj",
    }
    assert tool.status.status_code is StatusCode.UNSET


def test_only_the_stage_span_takes_the_run_ids(spans: InMemorySpanExporter) -> None:
    """A span started inside the stage span gets a fresh id, not the run's a second time."""
    with telemetry.stage_span("run-1", stage="dev"), telemetry.tool_span("run-1", "t"):
        pass
    finished = {span.name: span for span in spans.get_finished_spans()}
    tool = finished["execute_tool t"]
    assert tool.context is not None
    assert tool.context.span_id != telemetry.run_trace_ids("run-1")[1]


def test_a_failed_tool_call_is_an_error_span(spans: InMemorySpanExporter) -> None:
    with telemetry.tool_span("run-1", "t"):
        telemetry.record_tool_call(status_code=502, provider="mcp:jira")
    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert (span.attributes or {})["error.type"] == "502"


def test_a_bad_traceparent_falls_back_on_the_run(spans: InMemorySpanExporter) -> None:
    with telemetry.agent_span("run-1", trace_parent="garbage", agent="codex"):
        pass
    (span,) = spans.get_finished_spans()
    assert span.parent is not None
    assert span.parent.span_id == telemetry.run_trace_ids("run-1")[1]
