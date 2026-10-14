"""The deployment's kill switch for context reduction reaches the orchestrator (S37-03, ADR 0055)."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _orchestrator_env(*overrides: str) -> set[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=False)
    assert rendered.returncode == 0, rendered.stderr
    documents: list[dict[str, Any]] = [d for d in yaml.safe_load_all(rendered.stdout) if d]
    values = set()
    for deployment in documents:
        if deployment.get("kind") != "Deployment" or "orchestrator" not in deployment["metadata"]["name"]:
            continue
        for container in deployment["spec"]["template"]["spec"]["containers"]:
            for variable in container.get("env", []):
                if variable["name"] == "CHOREGOS_CONTEXT_REDUCTION":
                    values.add(variable["value"])
    return values


def test_on_by_default_so_the_policy_decides() -> None:
    assert _orchestrator_env() == {"on"}


def test_disabled_refuses_it_to_every_worker() -> None:
    assert _orchestrator_env("choregos-orchestrator.runner.contextReduction.enabled=false") == {"off"}
