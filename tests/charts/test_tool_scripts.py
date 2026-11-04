"""The deployment's kill switch for tool scripts reaches the orchestrator (S57-05, ADR 0122 §6)."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _orchestrator_env(*overrides: str) -> list[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rendered.returncode == 0, rendered.stderr
    documents: list[dict[str, Any]] = [d for d in yaml.safe_load_all(rendered.stdout) if d]
    values: list[str] = []
    for deployment in documents:
        if deployment.get("kind") != "Deployment" or "orchestrator" not in deployment["metadata"]["name"]:
            continue
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            for variable in container.get("env", []):
                if variable["name"] == "CHOREGOS_TOOL_SCRIPTS":
                    values.append(variable["value"])
    return values


def test_off_by_default_so_no_run_is_offered_the_bridge() -> None:
    values = _orchestrator_env()
    assert values and set(values) == {"off"}, "every orchestrator worker gets the switch, off"


def test_enabled_lets_the_ceiling_and_the_policy_decide() -> None:
    assert set(_orchestrator_env("choregos-orchestrator.runner.toolScripts.enabled=true")) == {"on"}


def test_a_values_file_without_the_section_renders_off() -> None:
    assert set(_orchestrator_env("choregos-orchestrator.runner.toolScripts=null")) == {"off"}
