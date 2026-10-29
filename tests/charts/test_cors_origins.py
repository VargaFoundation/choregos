"""The chart tells the API which browser origins may call it (S47-03).

`CHOREGOS_CORS_ORIGINS` defaults to the console's public URL; `choregos-api.corsOrigins` lists more.
The chart owns the variable, so `global.extraEnv` must not set it a second time.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
CORS = "CHOREGOS_CORS_ORIGINS"

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


def test_cors_origins_default_to_the_public_url() -> None:
    assert _api_env()[CORS]["value"] == "https://app.choregos.local"
    listed = _api_env('choregos-api.corsOrigins=["https://a.example","https://b.example"]')
    assert listed[CORS]["value"] == "https://a.example,https://b.example"


def test_an_extra_env_cors_value_fails_with_the_way_out() -> None:
    rendered = _render(f'global.extraEnv=[{{"name":"{CORS}","value":"https://x.example"}}]')
    assert rendered.returncode != 0, "the chart rendered two values for the CORS origins"
    assert "choregos-api.corsOrigins" in rendered.stderr
