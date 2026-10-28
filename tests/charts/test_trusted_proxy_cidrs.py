"""The chart tells the API which proxies' X-Forwarded-For entries to believe (S47-02).

The sign-in and webhook rate limit keys on the address the nearest trusted proxy saw. Without
`CHOREGOS_TRUSTED_PROXY_CIDRS` the API keys on its TCP peer, which behind the gateway and the
console's Next server is an in-cluster pod: every client would share one window. The chart owns the
variable, so `global.extraEnv` must not set it a second time.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
CIDRS = "CHOREGOS_TRUSTED_PROXY_CIDRS"
PRIVATE_RANGES = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*json_sets: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for value in json_sets:
        command += ["--set-json", value]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _api_env(*json_sets: str) -> dict[str, dict[str, Any]]:
    rendered = _render(*json_sets)
    assert rendered.returncode == 0, rendered.stderr[-600:]
    docs = [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]
    (api,) = [
        container
        for doc in docs
        if doc["kind"] in {"Deployment", "Rollout"} and doc["metadata"]["name"] == "choregos-api"
        for container in doc["spec"]["template"]["spec"]["containers"]
        if container["name"] == "api"
    ]
    return {item["name"]: item for item in api.get("env") or []}


def test_the_api_trusts_the_private_ranges_by_default() -> None:
    env = _api_env()
    assert json.loads(env[CIDRS]["value"]) == PRIVATE_RANGES
    assert env["CHOREGOS_TRUSTED_PROXY_HOPS"]["value"] == "0"
    assert json.loads(_api_env("choregos-api.trustedProxyCidrs=[]")[CIDRS]["value"]) == []


def test_extra_env_cannot_shadow_the_trusted_proxies() -> None:
    rendered = _render(f'global.extraEnv=[{{"name":"{CIDRS}","value":"[]"}}]')
    assert rendered.returncode != 0, "the chart rendered two values for the trusted proxies"
    assert "choregos-api.trustedProxyCidrs" in rendered.stderr, "the error says where to set it instead"
