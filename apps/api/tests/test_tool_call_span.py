"""Every call of the tool route is an `execute_tool` span of the run's trace (ADR 0018, S40-20)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from choregos_core import telemetry
from httpx import AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from .test_catalogue_outils import _intercepter, _run_avec_catalogue

pytestmark = pytest.mark.asyncio


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    telemetry.configure_tracing("choregos-api", exporter)
    yield exporter
    telemetry.shutdown_tracing()


def _provider_answers(monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    _intercepter(
        monkeypatch,
        httpx.Response(
            status_code,
            json={"profils": []},
            request=httpx.Request("GET", "https://api.annuaire.example/v1/search"),
        ),
        [],
    )


async def test_a_tool_call_is_a_child_of_the_stage_span(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    run_id, headers = await _run_avec_catalogue(project, tmp_path, monkeypatch)
    _provider_answers(monkeypatch, 200)

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils",
        headers=headers,
        json={"metier": "data engineer"},
    )

    assert response.status_code == 200, response.text
    (span,) = spans.get_finished_spans()
    assert span.name == "execute_tool recherche_profils"
    trace_id, stage_span_id = telemetry.run_trace_ids(run_id)
    assert span.context is not None and span.context.trace_id == trace_id
    assert span.parent is not None and span.parent.span_id == stage_span_id
    assert dict(span.attributes or {}) == {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "recherche_profils",
        "gen_ai.conversation.id": run_id,
        "gen_ai.provider.name": "annuaire",
        "run_id": run_id,
        "project": project["slug"],
        "work_item": "varga/x#9",
        "stage": "custom",
    }
    assert span.status.status_code is StatusCode.UNSET
    assert "data engineer" not in repr(span.to_json()), "a span never carries the tool's arguments"


async def test_a_failed_tool_call_is_an_error_span(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    run_id, headers = await _run_avec_catalogue(project, tmp_path, monkeypatch)
    _provider_answers(monkeypatch, 503)

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils",
        headers=headers,
        json={"metier": "data engineer"},
    )

    assert response.status_code == 200, response.text
    code = response.json()["status_code"]
    assert code >= 400
    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert (span.attributes or {})["error.type"] == str(code)


async def test_a_refused_tool_call_is_a_span_too(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spans: InMemorySpanExporter,
) -> None:
    run_id, headers = await _run_avec_catalogue(project, tmp_path, monkeypatch)

    response = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/not_in_the_catalogue", headers=headers, json={}
    )

    assert response.status_code == 404
    (span,) = spans.get_finished_spans()
    assert span.name == "execute_tool not_in_the_catalogue"
    assert span.status.status_code is StatusCode.ERROR
