"""S64-08b (ADR 0141 §4): the embedded LiteLLM's callback reaches the API it posts the ACP CLIs'
context breakdowns to.

The callback is off unless `CHOREGOS_CONTEXT_API_URL` names the API's internal routes; the platform
namespace denies egress by default, so the gateway's pods get a way to the API pods, on the API's port,
and nowhere else; an external gateway gets neither.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"

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


def _litellm_env(docs: list[dict[str, Any]]) -> dict[str, Any]:
    deployment = _named(docs, "Deployment", "choregos-litellm")
    assert deployment is not None
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    return {item["name"]: item.get("value") for item in container["env"]}


def test_the_callback_is_told_where_the_apis_internal_routes_are() -> None:
    env = _litellm_env(_docs("global.gateway.embedded=true"))
    assert env["CHOREGOS_CONTEXT_API_URL"] == "http://choregos-api:8000/api/v1/internal"
    # The callback signs each run's id with the gateway's master key.
    assert "LITELLM_MASTER_KEY" in env

    api = "http://api.platform.svc:9000"
    elsewhere = _litellm_env(_docs("global.gateway.embedded=true", f"global.internalApiUrl={api}"))
    assert elsewhere["CHOREGOS_CONTEXT_API_URL"] == api + "/api/v1/internal"


def test_the_gateways_pods_reach_the_api_pods_and_nothing_else() -> None:
    policy = _named(_docs("global.gateway.embedded=true"), "NetworkPolicy", "choregos-litellm-to-api")
    assert policy is not None
    assert policy["spec"]["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": "choregos-litellm"}}
    assert policy["spec"]["egress"] == [
        {
            "to": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "choregos-api"}}}],
            "ports": [{"protocol": "TCP", "port": 8000}],
        }
    ]


def test_an_external_gateway_gets_no_policy() -> None:
    assert _named(_docs(), "NetworkPolicy", "choregos-litellm-to-api") is None
