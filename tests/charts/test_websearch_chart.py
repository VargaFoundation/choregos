"""The optional `choregos-websearch` subchart (S80-17, ADR 0195): SearXNG, a search-only Firecrawl and
its Redis, and the egress relay when asked. Off by default; names adoptable; secrets never inline."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
ON = ("choregos-websearch.enabled=true",)
RELAY = (
    "choregos-websearch.relay.enabled=true",
    "choregos-websearch.relay.secretName=relay-secrets",
    "choregos-websearch.relay.organisation=acme",
)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*sets: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "c", str(CHART), "-f", str(CHART / "values" / "local.yaml")]
    for item in sets:
        command += ["--set", item]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _docs(*sets: str) -> list[dict[str, Any]]:
    rendered = _render(*sets)
    assert rendered.returncode == 0, rendered.stderr
    return [d for d in yaml.safe_load_all(rendered.stdout) if d]


def _names(docs: list[dict[str, Any]], kind: str) -> set[str]:
    return {d["metadata"]["name"] for d in docs if d.get("kind") == kind}


def test_off_by_default() -> None:
    docs = _docs()
    assert not {n for n in _names(docs, "Deployment") if "searxng" in n or "firecrawl" in n}


def test_search_only_without_relay() -> None:
    docs = _docs(*ON)
    assert {"c-searxng", "c-firecrawl", "c-firecrawl-redis"} <= _names(docs, "Deployment")
    assert "c-relay" not in _names(docs, "Deployment")
    firecrawl = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "c-firecrawl")
    container = firecrawl["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["node", "dist/src/index.js"]
    env = {e["name"]: e["value"] for e in container["env"]}
    assert env["SEARXNG_ENDPOINT"] == "http://c-searxng:8080"
    assert env["REDIS_URL"] == "redis://c-firecrawl-redis:6379"


def test_relay_needs_secret_and_organisation() -> None:
    assert "relay.secretName" in _render(*ON, "choregos-websearch.relay.enabled=true").stderr
    bad = _render(*ON, "choregos-websearch.relay.enabled=true", "choregos-websearch.relay.secretName=s")
    assert bad.returncode != 0 and "relay.organisation" in bad.stderr


def test_relay_reads_its_tokens_from_the_secret() -> None:
    docs = _docs(*ON, *RELAY)
    relay = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "c-relay")
    container = relay["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "-m", "choregos_api.relay", "serve"]
    refs = {e["name"]: e["valueFrom"]["secretKeyRef"] for e in container["env"] if "valueFrom" in e}
    assert refs["CHOREGOS_RELAY_API_TOKEN"] == {"name": "relay-secrets", "key": "api-token"}
    assert refs["CHOREGOS_RELAY_CLIENT_TOKEN"] == {"name": "relay-secrets", "key": "client-token"}
    config = next(d for d in docs if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "c-searxng-config")
    assert "c-relay:3128" in config["data"]["settings.yml"]
    assert "relay-secrets" not in config["data"]["settings.yml"]


def test_names_are_adoptable() -> None:
    docs = _docs(
        *ON, "choregos-websearch.names.firecrawl=firecrawl", "choregos-websearch.names.searxng=searxng"
    )
    assert {"firecrawl", "searxng"} <= _names(docs, "Service")
    firecrawl = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "firecrawl")
    env = {e["name"]: e["value"] for e in firecrawl["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["SEARXNG_ENDPOINT"] == "http://searxng:8080"


def test_pods_are_non_root_and_unprivileged() -> None:
    for doc in _docs(*ON, *RELAY):
        if doc["kind"] != "Deployment" or "websearch" not in doc["spec"]["template"]["metadata"][
            "labels"
        ].get("app.kubernetes.io/component", ""):
            continue
        spec = doc["spec"]["template"]["spec"]
        assert spec["securityContext"]["runAsNonRoot"] is True
        for container in spec["containers"] + spec.get("initContainers", []):
            assert container["securityContext"]["allowPrivilegeEscalation"] is False
