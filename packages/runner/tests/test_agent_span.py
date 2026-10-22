"""The runner's session is an `invoke_agent` span, child of the stage span (ADR 0018, S40-20)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageInput
from choregos_core import telemetry
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from .test_job import _client, _job

pytestmark = pytest.mark.asyncio

#: A parent the orchestrator could have sent: not the run's derived one, so the test sees it is read.
PARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    telemetry.configure_tracing("choregos-runner", exporter)
    yield exporter
    telemetry.shutdown_tracing()


async def test_the_session_hangs_off_the_trace_parent(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path, spans: InMemorySpanExporter
) -> None:
    job = _job(stage_input, tmp_path).model_copy(update={"trace_parent": PARENT})
    client = _client(job)

    outcome = await Runner(runner_settings, client).execute(job, client)  # type: ignore[arg-type]

    assert outcome.exit_code is Exit.OK, outcome.detail
    (span,) = spans.get_finished_spans()
    assert span.name == "invoke_agent job"
    assert span.context is not None and span.context.trace_id == 0x0AF7651916CD43DD8448EB211C80319C
    assert span.parent is not None and span.parent.span_id == 0xB7AD6B7169203331
    assert dict(span.attributes or {}) == {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "job",
        "gen_ai.conversation.id": job.run_id,
        "gen_ai.request.model": "none",
        "run_id": job.run_id,
        "project": job.project.slug,
        "work_item": job.work_item.key,
        "stage": "backtest",
        "choregos.stage.outcome": "done",
    }
    assert span.status.status_code is StatusCode.UNSET


async def test_without_a_trace_parent_the_session_joins_the_run_trace(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path, spans: InMemorySpanExporter
) -> None:
    job = _job(stage_input, tmp_path).model_copy(update={"trace_parent": None})
    client = _client(job)

    await Runner(runner_settings, client).execute(job, client)  # type: ignore[arg-type]

    (span,) = spans.get_finished_spans()
    trace_id, stage_span_id = telemetry.run_trace_ids(job.run_id)
    assert span.context is not None and span.context.trace_id == trace_id
    assert span.parent is not None and span.parent.span_id == stage_span_id


async def test_a_failed_session_is_an_error_span(
    stage_input: StageInput,
    runner_settings: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    monkeypatch.setenv("JOB_EXIT", "3")
    job = _job(stage_input, tmp_path)
    client = _client(job)

    await Runner(runner_settings, client).execute(job, client)  # type: ignore[arg-type]

    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert (span.attributes or {})["choregos.stage.outcome"] == "failed"
