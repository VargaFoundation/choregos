"""The orchestrator opens the root of a run's trace and hands its context to the runner (ADR 0018, S40-20)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from choregos_contracts import StageInput, StageResult, StageStatus
from choregos_core import telemetry
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from .conftest import Fixture
from .test_lost_run import _await, _ScriptedExecutor
from .test_result_from_journal import _journal

pytestmark = pytest.mark.asyncio


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    telemetry.configure_tracing("choregos-orchestrator", exporter)
    yield exporter
    telemetry.shutdown_tracing()


async def _prepared_and_started(setup: Fixture) -> tuple[str, StageInput]:
    from choregos_orchestrator.activities import stage as activities

    plan = await activities.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "actor": "dev",
            "from_state": "ready",
            "to_state": "in_progress",
            "attempt": 1,
        }
    )
    await activities.start_run(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "stage_input": plan["stage_input"],
        }
    )
    return str(plan["run_id"]), StageInput.model_validate(plan["stage_input"])


async def test_the_stage_input_carries_the_stage_span_context(setup: Fixture) -> None:
    """`StageInput.trace_parent` names the stage span: the runner's span hangs off it."""
    setup.adapters.executor = _ScriptedExecutor("running")
    run_id, stage_input = await _prepared_and_started(setup)
    assert stage_input.trace_parent == telemetry.traceparent(run_id)


async def test_the_wait_for_a_run_is_its_stage_span(setup: Fixture, spans: InMemorySpanExporter) -> None:
    setup.adapters.executor = _ScriptedExecutor("unknown")
    run_id, stage_input = await _prepared_and_started(setup)
    done = StageResult(status=StageStatus.DONE, summary="implemented")
    await _journal(run_id, {"stage_result": done.model_dump(mode="json", by_alias=True)})

    outcome = await _await(setup, run_id)

    assert outcome["status"] == "finished"
    (span,) = spans.get_finished_spans()
    assert span.name == "invoke_workflow implement"
    assert span.context is not None
    assert (span.context.trace_id, span.context.span_id) == telemetry.run_trace_ids(run_id)
    assert dict(span.attributes or {}) == {
        "gen_ai.operation.name": "invoke_workflow",
        "run_id": run_id,
        "project": stage_input.project.slug,
        "work_item": stage_input.work_item.key,
        "stage": "implement",
        "choregos.stage.outcome": "done",
    }
    assert span.status.status_code is StatusCode.UNSET


async def test_a_lost_run_is_an_error_stage_span(setup: Fixture, spans: InMemorySpanExporter) -> None:
    setup.adapters.executor = _ScriptedExecutor("running", "unknown")
    run_id, _ = await _prepared_and_started(setup)

    outcome = await _await(setup, run_id)

    assert outcome["status"] == "lost"
    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert (span.attributes or {})["choregos.stage.outcome"] == "lost"
    assert (span.attributes or {})["error.type"] == "lost"
