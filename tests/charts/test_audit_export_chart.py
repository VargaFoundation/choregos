"""The SIEM export of the audit log in the chart (S80-05b, ADR 0207): off, nothing; on, the API is told, the
token comes from a Secret by reference, and what the chart can see is wrong is refused at render time.

Each test renders the chart with `helm template`. The API's own `Settings` reads the rendered environment
back, so a name the chart writes and the code does not read turns a test red.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
HEC = "https://splunk.example:8088/services/collector/event"
ON = ["global.auditExport.enabled=true", f"global.auditExport.url={HEC}"]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def render(*sets: str) -> subprocess.CompletedProcess[str]:
    command = [
        "helm",
        "template",
        "choregos",
        str(CHART),
        "--set",
        "choregos-orchestrator.runner.skipOperatorCheck=true",
    ]
    for item in sets:
        command += ["--set", item]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def documents(*sets: str) -> list[dict[str, Any]]:
    done = render(*sets)
    assert done.returncode == 0, done.stderr
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def api_env(*sets: str) -> dict[str, dict[str, Any]]:
    [api] = [
        d for d in documents(*sets) if d["kind"] == "Deployment" and d["metadata"]["name"] == "choregos-api"
    ]
    return {e["name"]: e for e in api["spec"]["template"]["spec"]["containers"][0]["env"]}


def test_off_by_default_nothing_is_rendered() -> None:
    env = api_env()
    assert not [name for name in env if name.startswith("CHOREGOS_AUDIT_EXPORT")]
    policies = {
        d["metadata"]["name"]
        for d in documents("networkPolicies.enabled=true")
        if d["kind"] == "NetworkPolicy"
    }
    assert "choregos-audit-export-egress" not in policies


def test_on_the_api_is_told_and_the_token_is_a_secret_reference() -> None:
    env = api_env(
        *ON, "global.auditExport.token.secretRef=siem-token", "global.auditExport.token.scheme=Splunk"
    )
    assert env["CHOREGOS_AUDIT_EXPORT"]["value"] == "true"
    assert env["CHOREGOS_AUDIT_EXPORT_URL"]["value"] == HEC
    assert env["CHOREGOS_AUDIT_EXPORT_SCHEME"]["value"] == "Splunk"
    token = env["CHOREGOS_AUDIT_EXPORT_TOKEN"]
    assert "value" not in token
    assert token["valueFrom"]["secretKeyRef"] == {"name": "siem-token", "key": "audit-export-token"}


def test_the_api_reads_back_what_the_chart_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    from tranibor_api.audit_export.exporter import build_sink
    from tranibor_api.audit_export.sinks import HttpJsonSink, SyslogSink
    from tranibor_api.config import Settings

    env = api_env(
        *ON,
        "global.auditExport.format=splunk-hec",
        "global.auditExport.index=audit",
        "global.auditExport.organisations={a,b}",
        "global.auditExport.platform=false",
        "global.auditExport.intervalSeconds=30",
        "global.auditExport.batch=100",
    )
    for name, item in env.items():
        if name.startswith("CHOREGOS_AUDIT_EXPORT") and "value" in item:
            monkeypatch.setenv(name, item["value"])
    settings = Settings()
    assert settings.audit_export and settings.audit_export_sink == "https"
    assert settings.audit_export_orgs == ["a", "b"] and settings.audit_export_platform is False
    assert settings.audit_export_interval_s == 30 and settings.audit_export_batch == 100
    sink = build_sink(settings)
    assert isinstance(sink, HttpJsonSink) and sink.format == "splunk-hec" and sink.index == "audit"

    for name in list(env):
        monkeypatch.delenv(name, raising=False)
    syslog = api_env(
        "global.auditExport.enabled=true",
        "global.auditExport.sink=syslog",
        "global.auditExport.syslog.host=siem.example",
        "global.auditExport.format=cef",
    )
    for name, item in syslog.items():
        if name.startswith("CHOREGOS_AUDIT_EXPORT") and "value" in item:
            monkeypatch.setenv(name, item["value"])
    built = build_sink(Settings())
    assert isinstance(built, SyslogSink) and built.format == "cef" and built.port == 6514


def test_the_egress_policy_names_the_api_and_the_sink() -> None:
    policies = {
        d["metadata"]["name"]: d
        for d in documents(
            *ON, "networkPolicies.enabled=true", "global.auditExport.egress.cidrs={10.1.0.0/24}"
        )
        if d["kind"] == "NetworkPolicy"
    }
    policy = policies["choregos-audit-export-egress"]
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "choregos-api"}
    [rule] = policy["spec"]["egress"]
    assert rule["to"] == [{"ipBlock": {"cidr": "10.1.0.0/24"}}]
    assert rule["ports"] == [{"protocol": "TCP", "port": 443}]


@pytest.mark.parametrize(
    ("sets", "message"),
    [
        (
            ["global.auditExport.enabled=true", "global.auditExport.url=http://siem.example"],
            "https:// address",
        ),
        (["global.auditExport.enabled=true"], "https:// address"),
        ([*ON, "global.auditExport.format=xml"], "does not fit the https sink"),
        ([*ON, "global.auditExport.sink=udp"], "is not https or syslog"),
        (["global.auditExport.enabled=true", "global.auditExport.sink=syslog"], "syslog.host"),
        (
            [*ON, "global.extraEnv[0].name=CHOREGOS_AUDIT_EXPORT_URL", "global.extraEnv[0].value=x"],
            "global.auditExport",
        ),
    ],
)
def test_what_the_chart_can_see_is_wrong_is_refused(sets: list[str], message: str) -> None:
    done = render(*sets)
    assert done.returncode != 0 and message in done.stderr


def test_the_stalled_export_alert_exists_only_with_the_export_and_cites_emitted_metrics() -> None:
    from tranibor_api.metrics import NAMES

    def alerts(*sets: str) -> set[str]:
        rules = [d for d in documents(*sets) if d["kind"] == "PrometheusRule"]
        return {rule["alert"] for d in rules for g in d["spec"]["groups"] for rule in g["rules"]}

    assert "ChoregosAuditExportStalled" not in alerts()
    assert "ChoregosAuditExportStalled" in alerts(*ON)
    [rule] = [
        r
        for d in documents(*ON)
        if d["kind"] == "PrometheusRule"
        for g in d["spec"]["groups"]
        for r in g["rules"]
        if r["alert"] == "ChoregosAuditExportStalled"
    ]
    import re

    assert set(re.findall(r"choregos_[a-z_]+", rule_expr := rule["expr"])) <= NAMES, rule_expr
