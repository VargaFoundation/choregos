"""S39-07a: the chart tells every process which gateway adapter the deployment uses."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _gateway_types(*overrides: str) -> set[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    found: set[str] = set()
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for env in container.get("env", []):
                if env["name"] == "CHOREGOS_GATEWAY_TYPE":
                    found.add(env["value"])
    return found


def test_litellm_is_the_default_gateway_type() -> None:
    assert _gateway_types() == {"litellm"}


def test_the_configured_gateway_type_reaches_the_pods() -> None:
    assert _gateway_types("global.gateway.type=openai-compatible") == {"openai-compatible"}
