"""A run's trace through a real collector and a real Tempo, the gateway's spans included (S40-20b).

What no in-memory exporter proves: that LiteLLM loads the callback the chart ships, that the run's
key is what attaches its calls to the run's trace, that the bootstrap collector's configuration
hands the spans to Tempo, and that its span metrics carry every label the `traces` dashboard reads.
The stack is `tests/live/otel/compose.yaml` (a mocked model, no provider key):

    docker compose -p s4020b -f tests/live/otel/compose.yaml up -d --wait
    CHOREGOS_LIVE_OTEL=1 uv run pytest tests/live/test_otel_collector_live.py -m live
    docker compose -p s4020b -f tests/live/otel/compose.yaml down -v
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import httpx
import pytest
from choregos_core import telemetry

from .conftest import require

pytestmark = pytest.mark.live

ROOT = pathlib.Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "charts" / "choregos" / "dashboards" / "traces.json"
GATEWAY = os.environ.get("CHOREGOS_LIVE_OTEL_GATEWAY_URL", "http://127.0.0.1:14400")
# The compose file's master key, a local-only placeholder (assembled, not a literal key).
MASTER_KEY = "-".join(("sk", "local", "otel", "only"))
COLLECTOR = os.environ.get("CHOREGOS_LIVE_OTEL_COLLECTOR_URL", "http://127.0.0.1:14318")
METRICS = os.environ.get("CHOREGOS_LIVE_OTEL_METRICS_URL", "http://127.0.0.1:18889/metrics")
TEMPO = os.environ.get("CHOREGOS_LIVE_TEMPO_URL", "http://127.0.0.1:13200")
SENTINEL = "s4020b-prompt-sentinel"


def _eventually[T](probe: Callable[[], T | None], *, timeout_s: float, what: str) -> T:
    deadline = time.monotonic() + timeout_s
    while True:
        found = probe()
        if found is not None:
            return found
        if time.monotonic() > deadline:
            pytest.fail(f"{what}: nothing after {timeout_s:.0f} s")
        time.sleep(2)


def _hex(value: str, size: int) -> str:
    """Tempo's JSON gives ids in base64; an id already in hex passes as is."""
    if re.fullmatch(rf"[0-9a-f]{{{size * 2}}}", value):
        return value
    return base64.b64decode(value).hex()


def _attributes(span: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in span.get("attributes", []):
        value = item["value"]
        out[item["key"]] = next(iter(value.values())) if value else None
    return out


def _spans(trace: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(service name, span) for each span of a Tempo trace."""
    found = []
    for batch in trace.get("batches", []):
        resource = _attributes(batch.get("resource", {}))
        for scope in batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", [])):
            for span in scope.get("spans", []):
                found.append((str(resource.get("service.name")), span))
    return found


def _series(text: str, metric: str) -> list[dict[str, str]]:
    series = []
    for line in text.splitlines():
        if line.startswith(metric + "{"):
            labels = line[len(metric) + 1 : line.rindex("}")]
            series.append(dict(re.findall(r'(\w+)="((?:[^"\\]|\\.)*)"', labels)))
    return series


def _mint_run_key(client: httpx.Client, run_id: str) -> str:
    """The run's key, with the metadata `mint_run_key` writes on it."""
    response = client.post(
        f"{GATEWAY}/key/generate",
        headers={"Authorization": f"Bearer {MASTER_KEY}"},
        json={
            "models": ["platform/standard", "platform/broken"],
            "metadata": {
                "project": "proj",
                "work_item": "PROJ-1",
                "run_id": run_id,
                "transition": "t-dev",
                "role": "dev",
                "attempt": 1,
                "backend": "claude-code",
            },
        },
    )
    response.raise_for_status()
    return str(response.json()["key"])


def _chat(client: httpx.Client, key: str, model: str, **extra: Any) -> httpx.Response:
    return client.post(
        f"{GATEWAY}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": SENTINEL}], **extra},
    )


def test_a_runs_model_calls_land_in_its_trace_and_in_the_span_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require("CHOREGOS_LIVE_OTEL")
    run_id = f"PROJ-1-dev-{uuid4().hex[:8]}"
    trace_id, stage_span_id = telemetry.run_trace_ids(run_id)

    # The orchestrator's side: the stage span, through the same collector.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", COLLECTOR)
    assert telemetry.configure_tracing("choregos-orchestrator")
    try:
        with telemetry.stage_span(run_id, stage="dev", project="proj", work_item="PROJ-1"):
            pass
    finally:
        telemetry.shutdown_tracing()

    with httpx.Client(timeout=60) as client:
        key = _mint_run_key(client, run_id)
        answered = _chat(client, key, "platform/standard")
        assert answered.status_code == 200, answered.text[:300]
        # A request that claims another run: the key decides, not the request.
        claimed = _chat(client, key, "platform/standard", metadata={"run_id": "OTHER-9-dev-1"})
        assert claimed.status_code == 200, claimed.text[:300]
        refused = _chat(client, key, "platform/broken")
        assert refused.status_code == 429, refused.text[:300]
        # The platform's own key names no run: no span anywhere.
        _chat(client, MASTER_KEY, "platform/standard").raise_for_status()

        def trace() -> dict[str, Any] | None:
            response = client.get(f"{TEMPO}/api/traces/{trace_id:032x}")
            if response.status_code != 200:
                return None
            body: dict[str, Any] = response.json()
            gateway = [span for service, span in _spans(body) if service == "choregos-gateway"]
            return body if len(gateway) >= 3 else None

        body = _eventually(trace, timeout_s=90, what="the run's trace in Tempo")

    spans = _spans(body)
    (stage,) = [span for service, span in spans if service == "choregos-orchestrator"]
    assert _hex(stage["spanId"], 8) == f"{stage_span_id:016x}"
    gateway = [span for service, span in spans if service == "choregos-gateway"]
    assert len(gateway) == 3, [span["name"] for span in gateway]
    for span in gateway:
        assert _hex(span["traceId"], 16) == f"{trace_id:032x}"
        assert _hex(span["parentSpanId"], 8) == f"{stage_span_id:016x}"
        attributes = _attributes(span)
        assert attributes["run_id"] == run_id
        assert attributes["gen_ai.conversation.id"] == run_id
        assert attributes["stage"] == "dev"
        assert attributes["gen_ai.operation.name"] == "chat"
    answered_spans = [span for span in gateway if span["name"] == "chat platform/standard"]
    assert len(answered_spans) == 2
    for span in answered_spans:
        attributes = _attributes(span)
        assert attributes["gen_ai.provider.name"] == "openai"
        assert int(attributes["gen_ai.usage.input_tokens"]) > 0
        assert int(attributes["gen_ai.usage.output_tokens"]) > 0
    (failed,) = [span for span in gateway if span["name"] == "chat platform/broken"]
    assert failed["status"]["code"] in {2, "STATUS_CODE_ERROR"}
    assert _attributes(failed)["error.type"]
    assert SENTINEL not in json.dumps(body), "a prompt reached the trace"

    # The span metrics: every label the dashboard reads, on series the collector actually wrote.
    def scraped() -> str | None:
        text = httpx.get(METRICS, timeout=10).text
        gateway_series = [
            series
            for series in _series(text, "traces_span_metrics_calls_total")
            if series.get("service_name") == "choregos-gateway" and series.get("error_type")
        ]
        return text if gateway_series else None

    text = _eventually(scraped, timeout_s=120, what="the gateway's span metrics")
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    # The collector's targets: each panel's `tempo` twin reads Tempo's own span metrics (S40-20f).
    exprs = [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel["targets"]
        if target["refId"] == "collector"
    ]
    for metric in {name for expr in exprs for name in re.findall(r"\b(traces_\w+)", expr)}:
        written = _series(text, metric)
        assert written, f"{metric}: the dashboard reads it, the collector does not write it"
        labels = {label for series in written for label in series}
        for expr in exprs:
            if metric not in expr:
                continue
            read = set(re.findall(r"(\w+)\s*=~?\s*\"", expr))
            for group in re.findall(r"by \(([^)]*)\)", expr):
                read |= {label.strip() for label in group.split(",")}
            assert read <= labels, f"{sorted(read - labels)} read by {expr}, never written"
    stages = [
        series
        for series in _series(text, "traces_span_metrics_calls_total")
        if series.get("gen_ai_operation_name") == "invoke_workflow"
    ]
    assert any(series.get("stage") == "dev" for series in stages)
    assert not any("run_id" in series for series in _series(text, "traces_span_metrics_calls_total"))
