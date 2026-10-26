"""S40-20b (ADR 0018): the chart sends the platform's traces to the collector, the gateway's included.

Three properties. The embedded LiteLLM runs the span callback the tests prove, byte for byte. One
value, `global.tracing.otlpEndpoint`, reaches every process that traces (the API, each worker, the
migration Job, the gateway) and nothing renders when it is empty. The bootstrap collector exports to
Tempo and derives the span metrics, and every label the `traces` dashboard reads is one it produces.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
CHART_COPY = CHART / "charts" / "litellm" / "files" / "choregos_llm_spans.py"
SOURCE = ROOT / "packages" / "core" / "src" / "choregos_core" / "llm_spans.py"
COLLECTOR = ROOT / "infra" / "bootstrap" / "otel-collector.yaml"
DASHBOARD = CHART / "dashboards" / "traces.json"
ENDPOINT = "http://otel-collector.monitoring.svc:4318"

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*overrides: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART), "--set", "global.gateway.embedded=true"]
    for override in overrides:
        command += ["--set", override]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _docs(*overrides: str) -> list[dict[str, Any]]:
    rendered = _render(*overrides)
    assert rendered.returncode == 0, rendered.stderr[-400:]
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _litellm(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {doc["kind"]: doc for doc in docs if doc["metadata"].get("name") == "choregos-litellm"}


def _tracing_processes(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each process that traces, by name: the platform's (they read `CHOREGOS_ENV`) and the gateway."""
    processes: dict[str, dict[str, Any]] = {}
    for doc in docs:
        if doc["kind"] not in {"Deployment", "Job"}:
            continue
        for container in doc["spec"]["template"]["spec"].get("containers", []):
            env = {item["name"]: item.get("value") for item in container.get("env") or []}
            if "CHOREGOS_ENV" in env or doc["metadata"]["name"] == "choregos-litellm":
                processes[f"{doc['metadata']['name']}/{container['name']}"] = env
    return processes


def test_the_chart_ships_the_callback_the_tests_prove_byte_for_byte() -> None:
    """`choregos_core.llm_spans` is tested; the chart's copy is what LiteLLM runs."""
    assert CHART_COPY.read_bytes() == SOURCE.read_bytes(), (
        f"copy {SOURCE.relative_to(ROOT)} to {CHART_COPY.relative_to(ROOT)}"
    )


@needs_helm
def test_litellm_loads_the_span_callback_next_to_its_config() -> None:
    docs = _litellm(_docs())
    data = docs["ConfigMap"]["data"]
    assert data["choregos_llm_spans.py"] == SOURCE.read_text(encoding="utf-8")
    settings = yaml.safe_load(data["config.yaml"])["litellm_settings"]
    assert settings["callbacks"] == ["choregos_llm_spans.handler"]


@needs_helm
def test_the_endpoint_reaches_every_process_that_traces() -> None:
    processes = _tracing_processes(_docs(f"global.tracing.otlpEndpoint={ENDPOINT}"))
    assert any(name.startswith("choregos-litellm/") for name in processes), sorted(processes)
    assert any("migration" in name for name in processes), sorted(processes)
    assert any("orchestrator" in name for name in processes), sorted(processes)
    missing = sorted(
        name for name, env in processes.items() if env.get("OTEL_EXPORTER_OTLP_ENDPOINT") != ENDPOINT
    )
    assert not missing, f"without the endpoint: {missing}"


@needs_helm
def test_without_an_endpoint_nothing_traces() -> None:
    processes = _tracing_processes(_docs())
    assert processes
    assert not [name for name, env in processes.items() if "OTEL_EXPORTER_OTLP_ENDPOINT" in env]


@needs_helm
def test_the_endpoint_cannot_be_set_twice_through_extra_env() -> None:
    rendered = _render(
        f"global.tracing.otlpEndpoint={ENDPOINT}",
        "global.extraEnv[0].name=OTEL_EXPORTER_OTLP_ENDPOINT",
        "global.extraEnv[0].value=http://elsewhere:4318",
    )
    assert rendered.returncode != 0, "the chart rendered two values for OTEL_EXPORTER_OTLP_ENDPOINT"
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" in rendered.stderr


@needs_helm
def test_the_endpoint_set_through_extra_env_before_s40_20b_still_renders() -> None:
    """The documented way until S40-20b: an install that used it must not stop rendering."""
    rendered = _render(
        "global.extraEnv[0].name=OTEL_EXPORTER_OTLP_ENDPOINT", f"global.extraEnv[0].value={ENDPOINT}"
    )
    assert rendered.returncode == 0, rendered.stderr[-400:]


@needs_helm
def test_a_new_endpoint_or_callback_rolls_the_gateway() -> None:
    def checksum(*overrides: str) -> str:
        deployment = _litellm(_docs(*overrides))["Deployment"]
        return str(deployment["spec"]["template"]["metadata"]["annotations"]["checksum/config"])

    assert checksum() != checksum(f"global.tracing.otlpEndpoint={ENDPOINT}")


def _collector() -> dict[str, Any]:
    app = yaml.safe_load(COLLECTOR.read_text(encoding="utf-8"))
    values: dict[str, Any] = yaml.safe_load(app["spec"]["source"]["helm"]["values"])
    return values


def test_the_collector_is_the_endpoint_the_chart_documents() -> None:
    """The value's comment names the bootstrap collector's Service; the two must agree."""
    values = _collector()
    assert values["mode"] == "deployment"
    assert values["image"]["repository"], "the chart refuses to render without an image"
    assert f"http://{values['fullnameOverride']}.monitoring.svc:4318" == ENDPOINT
    assert ENDPOINT in (CHART / "values.yaml").read_text(encoding="utf-8")


def test_the_collector_sends_every_span_to_tempo_and_to_the_span_metrics() -> None:
    config = _collector()["config"]
    pipelines = config["service"]["pipelines"]
    assert pipelines["traces"]["receivers"] == ["otlp"]
    assert set(pipelines["traces"]["exporters"]) == {"otlp/tempo", "spanmetrics"}
    assert config["exporters"]["otlp/tempo"]["endpoint"] == "tempo.monitoring.svc:4317"
    assert pipelines["metrics"]["receivers"] == ["spanmetrics"]
    assert pipelines["metrics"]["exporters"] == ["prometheus"]


def test_no_span_metric_has_one_series_per_run() -> None:
    dimensions = {item["name"] for item in _collector()["config"]["connectors"]["spanmetrics"]["dimensions"]}
    assert not dimensions & {"run_id", "work_item", "gen_ai.conversation.id"}


def _produced_labels() -> set[str]:
    """The labels the collector's Prometheus exporter writes on a span metric."""
    dimensions = _collector()["config"]["connectors"]["spanmetrics"]["dimensions"]
    defaults = {"service.name", "span.name", "span.kind", "status.code"}
    return {re.sub(r"[^a-zA-Z0-9_]", "_", name) for name in defaults | {d["name"] for d in dimensions}} | {
        "le"
    }


#: The labels Tempo's metrics generator puts on every span metric (S40-20f). The `tempo` targets may
#: read these and the collector's dimensions, which the generator must be given as its
#: `span_metrics.dimensions` (`docs/deployment.md`, Traces): without them, the panels draw nothing.
TEMPO_INTRINSICS = {"service", "span_name", "span_kind", "status_code", "le"}


def _targets(ref: str) -> list[str]:
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    return [
        target["expr"]
        for panel in dashboard["panels"]
        for target in panel["targets"]
        if target["refId"] == ref
    ]


def _exprs() -> list[str]:
    return _targets("collector")


def _labels(expr: str) -> set[str]:
    labels = set(re.findall(r"(\w+)\s*=~?\s*\"", expr))
    for group in re.findall(r"by \(([^)]*)\)", expr):
        labels |= {label.strip() for label in group.split(",")}
    return labels


def test_every_traces_panel_reads_a_label_the_collector_produces() -> None:
    """No orphan panel (ADR 0018): a label not produced is a panel that draws nothing, silently."""
    namespace = _collector()["config"]["connectors"]["spanmetrics"]["namespace"].replace(".", "_")
    metrics = {f"{namespace}_calls_total", f"{namespace}_duration_milliseconds_bucket"}
    produced = _produced_labels()
    exprs = _exprs()
    assert exprs
    for expr in exprs:
        used_metrics = set(re.findall(r"\b(traces_\w+)", expr))
        assert used_metrics and used_metrics <= metrics, expr
        assert _labels(expr) <= produced, f"{sorted(_labels(expr) - produced)} in {expr}"


def test_every_traces_panel_also_reads_tempos_metrics_generator() -> None:
    """S40-20f: an install without the collector (Tempo's own span metrics, as on dev) draws too.

    Each panel has a `tempo` twin of its `collector` target: Tempo's names (`traces_spanmetrics_*`,
    latency in seconds, `service` for `service_name`), the same dimensions, the same legend."""
    dashboard = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    dimensions = {
        re.sub(r"[^a-zA-Z0-9_]", "_", d["name"])
        for d in _collector()["config"]["connectors"]["spanmetrics"]["dimensions"]
    }
    for panel in dashboard["panels"]:
        targets = {target["refId"]: target for target in panel["targets"]}
        assert set(targets) == {"collector", "tempo"}, panel["title"]
        collector, tempo = targets["collector"], targets["tempo"]
        assert tempo["legendFormat"] == collector["legendFormat"], panel["title"]
        used = set(re.findall(r"\b(traces_\w+)", tempo["expr"]))
        assert used and used <= {"traces_spanmetrics_calls_total", "traces_spanmetrics_latency_bucket"}, tempo
        assert _labels(tempo["expr"]) <= TEMPO_INTRINSICS | dimensions, tempo["expr"]
        expected = _labels(collector["expr"]) - {"service_name"} | (
            {"service"} if "service_name" in _labels(collector["expr"]) else set()
        )
        assert _labels(tempo["expr"]) == expected, panel["title"]
        if "latency_bucket" in tempo["expr"]:
            assert tempo["expr"].endswith("* 1000"), "Tempo's latency is in seconds, the panels in ms"


def test_the_gateway_panels_name_the_gateways_service() -> None:
    from choregos_core import llm_spans

    assert any(f'service_name="{llm_spans.SERVICE_NAME}"' in expr for expr in _exprs())
    assert any(f'service="{llm_spans.SERVICE_NAME}"' in expr for expr in _targets("tempo"))


LIVE = ROOT / "tests" / "live" / "otel"


def test_the_live_collector_runs_the_bootstrap_configuration() -> None:
    """`tests/live/otel/collector.yaml` is what the live test proves: the bootstrap's, rendered."""
    live = yaml.safe_load((LIVE / "collector.yaml").read_text(encoding="utf-8"))
    bootstrap = _collector()["config"]
    assert live["connectors"] == bootstrap["connectors"]
    for name, exporter in bootstrap["exporters"].items():
        assert live["exporters"][name] == exporter, name
    pipelines = {name: pipeline for name, pipeline in bootstrap["service"]["pipelines"].items() if pipeline}
    assert live["service"]["pipelines"] == pipelines


@needs_helm
def test_the_live_gateway_loads_the_charts_callbacks() -> None:
    chart = yaml.safe_load(_litellm(_docs())["ConfigMap"]["data"]["config.yaml"])["litellm_settings"]
    live = yaml.safe_load((LIVE / "litellm.yaml").read_text(encoding="utf-8"))["litellm_settings"]
    assert live["callbacks"] == chart["callbacks"]
