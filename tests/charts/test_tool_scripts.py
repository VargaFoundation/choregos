"""The deployment's default for tool scripts (S57-05) and its hard brake (S57-05b) reach the orchestrator
(ADR 0122 §6, amended 2026-11-05): `runner.toolScripts.enabled` is the default of an organisation that does
not choose, `runner.toolScripts.allowed: false` refuses them to every organisation."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _orchestrator_env(*overrides: str, variable: str = "CHOREGOS_TOOL_SCRIPTS") -> list[str]:
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
            for env in container.get("env", []):
                if env["name"] == variable:
                    values.append(env["value"])
    return values


def test_off_by_default_so_no_run_is_offered_the_bridge() -> None:
    values = _orchestrator_env()
    assert values and set(values) == {"off"}, "every orchestrator worker gets the switch, off"


def test_enabled_lets_the_ceiling_and_the_policy_decide() -> None:
    assert set(_orchestrator_env("choregos-orchestrator.runner.toolScripts.enabled=true")) == {"on"}


def test_a_values_file_without_the_section_renders_off() -> None:
    assert set(_orchestrator_env("choregos-orchestrator.runner.toolScripts=null")) == {"off"}


def test_the_brake_is_released_by_default_so_each_organisation_decides() -> None:
    values = _orchestrator_env(variable="CHOREGOS_TOOL_SCRIPTS_ALLOWED")
    assert values and set(values) == {"on"}, "every orchestrator worker gets the brake, released"
    assert len(values) == len(_orchestrator_env()), "beside the default, on the same workers"


def test_allowed_false_pulls_the_brake_whatever_the_default() -> None:
    braked = (
        "choregos-orchestrator.runner.toolScripts.allowed=false",
        "choregos-orchestrator.runner.toolScripts.enabled=true",
    )
    assert set(_orchestrator_env(*braked, variable="CHOREGOS_TOOL_SCRIPTS_ALLOWED")) == {"off"}
    assert set(_orchestrator_env(*braked)) == {"on"}, "the default is rendered as written"


def test_a_values_file_without_the_section_leaves_the_brake_released() -> None:
    overrides = ("choregos-orchestrator.runner.toolScripts=null",)
    assert set(_orchestrator_env(*overrides, variable="CHOREGOS_TOOL_SCRIPTS_ALLOWED")) == {"on"}
