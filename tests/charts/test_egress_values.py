# SPDX-License-Identifier: Apache-2.0
"""`global.egress.*` reaches the API and the workers as the three environment variables the
organisation egress allowlist reads (S39-22a, ADR 0066). Rendered with Helm and read in Python: the
helm-unittest plugin of this machine is stale (AGENTS.md)."""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*sets: str) -> list[dict[str, object]]:
    command = ["helm", "template", "choregos", str(CHART)]
    for value in sets:
        command += ["--set-json", value]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr[-600:]
    return [doc for doc in yaml.safe_load_all(done.stdout) if doc]


def _env(docs: list[dict[str, object]], deployment: str) -> dict[str, str]:
    for doc in docs:
        if doc.get("kind") == "Deployment" and str(doc["metadata"]["name"]).endswith(deployment):  # type: ignore[index]
            containers = doc["spec"]["template"]["spec"]["containers"]  # type: ignore[index]
            return {e["name"]: e.get("value", "") for c in containers for e in (c.get("env") or [])}
    raise AssertionError(f"no Deployment named *{deployment}")


def test_the_default_is_enforce_with_no_ceiling() -> None:
    docs = _render()
    for deployment in ("api", "orchestrator"):
        env = _env(docs, deployment)
        assert env["CHOREGOS_EGRESS_MODE"] == "enforce"
        assert "CHOREGOS_EGRESS_CEILING" not in env and "CHOREGOS_EGRESS_PLATFORM_CHANGE" not in env


def test_the_ceiling_and_the_platform_change_reach_the_api_and_the_workers() -> None:
    ceiling = {"api": ["api.github.com"], "workers": ["*.atlassian.net"]}
    change = {"format": "text", "owner": "the platform team", "template": "add {{hosts_csv}}"}
    docs = _render(
        'global.egress.mode="warn"',
        f"global.egress.ceiling={json.dumps(ceiling)}",
        f"global.egress.platformChange={json.dumps(change)}",
    )
    for deployment in ("api", "orchestrator"):
        env = _env(docs, deployment)
        assert env["CHOREGOS_EGRESS_MODE"] == "warn"
        assert json.loads(env["CHOREGOS_EGRESS_CEILING"]) == ceiling
        assert json.loads(env["CHOREGOS_EGRESS_PLATFORM_CHANGE"]) == change


def test_extra_env_cannot_shadow_the_egress_settings() -> None:
    done = subprocess.run(
        [
            "helm",
            "template",
            "choregos",
            str(CHART),
            "--set-json",
            'global.extraEnv=[{"name":"CHOREGOS_EGRESS_MODE","value":"off"}]',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0 and "CHOREGOS_EGRESS_MODE" in done.stderr
