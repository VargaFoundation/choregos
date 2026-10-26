"""S40-20c (ADR 0018 point 3): the chart runs the nightly ledger-vs-trace job when Tempo is named.

One value, `global.tracing.tempoUrl`, renders the CronJob and opens Tempo's query port to its pods,
and only to them; empty, nothing renders.
"""

from __future__ import annotations

import importlib
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
TEMPO = "http://tempo.monitoring.svc:3200"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _docs(*overrides: str) -> list[dict[str, Any]]:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rendered.returncode == 0, rendered.stderr[-400:]
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _named(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any] | None:
    return next((d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name), None)


def test_without_tempo_there_is_no_job_nor_opening() -> None:
    docs = _docs()
    assert _named(docs, "CronJob", "choregos-trace-drift") is None
    assert _named(docs, "NetworkPolicy", "choregos-trace-drift-tempo") is None


def test_the_job_runs_the_comparison_against_the_named_tempo() -> None:
    docs = _docs(f"global.tracing.tempoUrl={TEMPO}")
    job = _named(docs, "CronJob", "choregos-trace-drift")
    assert job is not None
    assert job["spec"]["concurrencyPolicy"] == "Forbid"
    pod = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    (container,) = pod["containers"]
    assert container["command"][:3] == ["python", "-m", "choregos_api.trace_drift"]
    importlib.import_module(container["command"][2])
    env = {item["name"]: item for item in container["env"]}
    assert env["CHOREGOS_TEMPO_URL"]["value"] == TEMPO
    assert "CHOREGOS_DATABASE_URL" in env, "the job reads the ledger"


def test_only_the_job_s_pods_reach_tempo_s_query_port() -> None:
    docs = _docs("global.tracing.tempoUrl=http://tempo.monitoring.svc:3100")
    job = _named(docs, "CronJob", "choregos-trace-drift")
    policy = _named(docs, "NetworkPolicy", "choregos-trace-drift-tempo")
    assert job is not None and policy is not None
    labels = job["spec"]["jobTemplate"]["spec"]["template"]["metadata"]["labels"]
    selector = policy["spec"]["podSelector"]["matchLabels"]
    assert selector == {"app.kubernetes.io/name": "choregos-trace-drift"}
    assert selector.items() <= labels.items()
    (rule,) = policy["spec"]["egress"]
    assert rule["ports"] == [{"protocol": "TCP", "port": 3100}]
    assert rule["to"] == [
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}}
    ]
    platform = _named(docs, "NetworkPolicy", "choregos-allow-platform")
    assert platform is not None
    opened = {p["port"] for r in platform["spec"]["egress"] for p in r.get("ports", [])}
    assert 3100 not in opened, "the platform's other pods do not read traces"


def _namespace_of(rule: dict[str, Any]) -> str:
    (to,) = rule["to"]
    return str(to["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"])


def test_the_policies_open_tracing_where_the_endpoints_live() -> None:
    """S40-20e: a platform whose Tempo is not in `monitoring` (the Diametral dev cluster: `tempo`)."""
    docs = _docs(
        "global.tracing.otlpEndpoint=http://tempo.tempo.svc.cluster.local:4318",
        "global.tracing.tempoUrl=http://tempo.tempo.svc.cluster.local:3200",
    )
    drift = _named(docs, "NetworkPolicy", "choregos-trace-drift-tempo")
    platform = _named(docs, "NetworkPolicy", "choregos-allow-platform")
    assert drift is not None and platform is not None
    (rule,) = drift["spec"]["egress"]
    assert _namespace_of(rule) == "tempo" and rule["ports"] == [{"protocol": "TCP", "port": 3200}]
    (otlp,) = [
        r for r in platform["spec"]["egress"] if {"protocol": "TCP", "port": 4318} in r.get("ports", [])
    ]
    assert _namespace_of(otlp) == "tempo"


def test_a_host_without_a_namespace_keeps_the_bootstrap_s() -> None:
    docs = _docs(
        "global.tracing.otlpEndpoint=http://collector:4318", "global.tracing.tempoUrl=http://tempo:3200"
    )
    drift = _named(docs, "NetworkPolicy", "choregos-trace-drift-tempo")
    platform = _named(docs, "NetworkPolicy", "choregos-allow-platform")
    assert drift is not None and platform is not None
    assert _namespace_of(drift["spec"]["egress"][0]) == "monitoring"
    (otlp,) = [
        r for r in platform["spec"]["egress"] if {"protocol": "TCP", "port": 4318} in r.get("ports", [])
    ]
    assert _namespace_of(otlp) == "monitoring"
