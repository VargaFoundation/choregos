# SPDX-License-Identifier: Apache-2.0
"""The nightly ledger-vs-trace comparison (ADR 0018 point 3, S40-20c)."""

from __future__ import annotations

import itertools
from datetime import timedelta
from typing import Any

import httpx
import pytest

_KEYS = itertools.count(1)


def _span(name: str, tokens_in: int | None, tokens_out: int | None) -> dict[str, Any]:
    attributes = [{"key": "gen_ai.operation.name", "value": {"stringValue": "chat"}}]
    if tokens_in is not None:
        # OTLP/JSON writes a 64-bit integer as a string.
        attributes.append({"key": "gen_ai.usage.input_tokens", "value": {"intValue": str(tokens_in)}})
    if tokens_out is not None:
        attributes.append({"key": "gen_ai.usage.output_tokens", "value": {"intValue": tokens_out}})
    return {"name": name, "attributes": attributes}


def _trace(*spans: dict[str, Any], service: str = "choregos-gateway") -> dict[str, Any]:
    return {
        "batches": [
            {
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": service}}]},
                "scopeSpans": [{"spans": list(spans)}],
            }
        ]
    }


class FakeTempo:
    """Traces by run id; a run named in `failing` answers an error."""

    def __init__(self) -> None:
        self.by_run: dict[str, dict[str, Any]] = {}
        self.failing: set[str] = set()
        self.asked: list[str] = []

    def put(self, run_id: str, body: dict[str, Any]) -> None:
        self.by_run[run_id] = body

    async def trace(self, trace_id: str) -> dict[str, Any] | None:
        from choregos_api.trace_drift import TempoError
        from choregos_core.telemetry import run_trace_ids

        self.asked.append(trace_id)
        for run_id in self.failing:
            if f"{run_trace_ids(run_id)[0]:032x}" == trace_id:
                raise TempoError("Tempo answered 500")
        for run_id, body in self.by_run.items():
            if f"{run_trace_ids(run_id)[0]:032x}" == trace_id:
                return body
        return None


async def _run(
    project_id: str,
    *,
    tokens: tuple[int, int] | None,
    ended_hours_ago: float = 2,
    kind: str = "model",
    **fields: Any,
) -> str:
    from choregos_api.db.models import CostLedger, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    ended = utcnow() - timedelta(hours=ended_hours_ago)
    async with session_scope(orgs="*") as session:
        item = WorkItem(project_id=project_id, tracker_key=f"DRIFT-{next(_KEYS)}", title="T", state="done")
        session.add(item)
        await session.flush()
        run = Run(
            work_item_id=item.id,
            project_id=project_id,
            stage_role="implement",
            attempt=1,
            status="succeeded",
            ended_at=ended,
            spend_collected=fields.pop("spend_collected", True),
            **fields,
        )
        session.add(run)
        await session.flush()
        if tokens is not None:
            session.add(
                CostLedger(
                    project_id=project_id,
                    work_item_id=item.id,
                    run_id=run.id,
                    kind=kind,
                    tokens_in=tokens[0],
                    tokens_out=tokens[1],
                    ts=ended,
                )
            )
            # A tool line of the same run: it carries no tokens and does not count.
            session.add(
                CostLedger(project_id=project_id, work_item_id=item.id, run_id=run.id, kind="tool", ts=ended)
            )
        return str(run.id)


async def _compare(tempo: FakeTempo, tolerance: float = 0.01) -> Any:
    from choregos_api.trace_drift import compare
    from choregos_core import utcnow

    now = utcnow()
    return await compare(tempo, since=now - timedelta(hours=24), until=now, tolerance=tolerance)


async def _drift_lines() -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        return list(
            (await session.execute(select(AuditLog).where(AuditLog.action == "trace.drift"))).scalars()
        )


async def test_a_trace_that_agrees_with_the_ledger_records_nothing(project: dict[str, Any]) -> None:
    tempo = FakeTempo()
    run = await _run(project["id"], tokens=(1200, 300))
    tempo.put(run, _trace(_span("chat a", 1000, 200), _span("chat b", 200, 100)))
    report = await _compare(tempo)
    assert (report.runs, report.matched, report.drift, report.missing) == (1, 1, 0, 0)
    assert await _drift_lines() == []


async def test_a_drift_is_recorded_once_as_a_platform_finding(project: dict[str, Any]) -> None:
    tempo = FakeTempo()
    run = await _run(project["id"], tokens=(1200, 300))
    tempo.put(run, _trace(_span("chat a", 1000, 200)))
    report = await _compare(tempo)
    assert (report.drift, report.recorded) == (1, 1)
    (line,) = await _drift_lines()
    assert line.org_id is None, "a platform finding, read by a platform session only"
    assert (line.actor_kind, line.target_type, line.target_id) == ("system", "run", run[:64])
    assert line.payload["run_id"] == run
    assert line.payload["ledger"] == {"tokens_in": 1200, "tokens_out": 300}
    assert line.payload["trace"] == {"tokens_in": 1000, "tokens_out": 200, "spans": 1}
    assert line.payload["stage"] == "implement"
    again = await _compare(tempo)
    assert (again.drift, again.recorded) == (1, 0), "the next night sees it and writes nothing more"
    assert len(await _drift_lines()) == 1


async def test_the_ledger_is_never_corrected(project: dict[str, Any]) -> None:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    tempo = FakeTempo()
    run = await _run(project["id"], tokens=(500, 50))
    tempo.put(run, _trace(_span("chat", 900, 90)))
    await _compare(tempo)
    async with session_scope(orgs="*") as session:
        total = (
            await session.execute(select(func.sum(CostLedger.tokens_in)).where(CostLedger.run_id == run))
        ).scalar_one()
    assert total == 500


async def test_a_missing_trace_is_counted_not_recorded(project: dict[str, Any]) -> None:
    tempo = FakeTempo()
    await _run(project["id"], tokens=(10, 5))
    traced_without_gateway = await _run(project["id"], tokens=(10, 5))
    tempo.put(
        traced_without_gateway, _trace(_span("invoke_workflow", None, None), service="choregos-orchestrator")
    )
    report = await _compare(tempo)
    assert (report.runs, report.missing, report.drift) == (2, 2, 0)
    assert await _drift_lines() == []


async def test_only_the_gateway_s_spans_count(project: dict[str, Any]) -> None:
    from choregos_api.trace_drift import gateway_usage

    run = await _run(project["id"], tokens=(100, 10))
    body = _trace(_span("chat", 100, 10))
    body["batches"] += _trace(_span("chat", 5000, 5000), service="choregos-runner")["batches"]
    tempo = FakeTempo()
    tempo.put(run, body)
    report = await _compare(tempo)
    assert report.matched == 1, "a span another process wrote adds no tokens"
    assert gateway_usage(body).spans == 1


async def test_the_tolerance_is_a_share_of_the_ledger(project: dict[str, Any]) -> None:
    tempo = FakeTempo()
    near = await _run(project["id"], tokens=(10_000, 1_000))
    tempo.put(near, _trace(_span("chat", 10_050, 1_000)))
    assert (await _compare(tempo, tolerance=0.01)).matched == 1
    assert (await _compare(tempo, tolerance=0.001)).drift == 1


async def test_the_window_reads_only_collected_runs_ended_in_it(project: dict[str, Any]) -> None:
    tempo = FakeTempo()
    old = await _run(project["id"], tokens=(10, 5), ended_hours_ago=30)
    uncollected = await _run(project["id"], tokens=(10, 5), spend_collected=False)
    no_ledger = await _run(project["id"], tokens=None)
    for run in (old, uncollected, no_ledger):
        tempo.put(run, _trace(_span("chat", 1, 1)))
    report = await _compare(tempo)
    assert report.runs == 0
    assert tempo.asked == []


async def test_a_remote_agent_s_line_is_not_compared(project: dict[str, Any]) -> None:
    tempo = FakeTempo()
    await _run(project["id"], tokens=(0, 0), kind="external")
    report = await _compare(tempo)
    assert report.runs == 0, "a remote agent's spend never went through the gateway: no trace to read"
    assert tempo.asked == []


async def test_a_tempo_error_is_reported_and_the_others_are_still_read(project: dict[str, Any]) -> None:
    tempo = FakeTempo()
    broken = await _run(project["id"], tokens=(10, 5))
    fine = await _run(project["id"], tokens=(10, 5))
    tempo.failing.add(broken)
    tempo.put(fine, _trace(_span("chat", 10, 5)))
    report = await _compare(tempo)
    assert report.matched == 1
    assert len(report.errors) == 1 and broken in report.errors[0]


async def test_the_reader_tells_a_missing_trace_from_an_error() -> None:
    from choregos_api.trace_drift import TempoError, TempoReader

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/" + "a" * 32):
            return httpx.Response(200, json=_trace(_span("chat", 1, 1)))
        if request.url.path.endswith("/" + "b" * 32):
            return httpx.Response(404, text="trace not found")
        return httpx.Response(500, text="boom")

    reader = TempoReader("http://tempo:3200/", transport=httpx.MockTransport(answer))
    try:
        assert (await reader.trace("a" * 32)) is not None
        assert (await reader.trace("b" * 32)) is None
        with pytest.raises(TempoError, match="500"):
            await reader.trace("c" * 32)
    finally:
        await reader.aclose()


def test_the_otlp_resource_spans_shape_is_read_too() -> None:
    from choregos_api.trace_drift import gateway_usage

    body = {"trace": {"resourceSpans": _trace(_span("chat", 7, 3))["batches"]}}
    assert (gateway_usage(body).tokens_in, gateway_usage(body).tokens_out) == (7, 3)


async def test_without_a_tempo_address_the_job_refuses(
    app: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from choregos_api import trace_drift
    from choregos_api.config import reset_settings_cache

    monkeypatch.delenv("CHOREGOS_TEMPO_URL", raising=False)
    reset_settings_cache()
    assert await trace_drift._main(24, 0) == 2
    assert "CHOREGOS_TEMPO_URL" in capsys.readouterr().err


async def test_the_job_fails_when_tempo_errs_and_prints_its_report(
    project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from choregos_api import trace_drift
    from choregos_api.config import reset_settings_cache

    await _run(project["id"], tokens=(10, 5))
    monkeypatch.setenv("CHOREGOS_TEMPO_URL", "http://tempo.invalid:3200")
    reset_settings_cache()

    class Down(trace_drift.TempoReader):
        async def trace(self, trace_id: str) -> dict[str, Any] | None:
            raise trace_drift.TempoError("Tempo unreachable")

    monkeypatch.setattr(trace_drift, "TempoReader", Down)
    assert await trace_drift._main(24, 0) == 1
    # The report is the last line; the log lines before it go to the same stream.
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert report["runs"] == 1 and len(report["errors"]) == 1
