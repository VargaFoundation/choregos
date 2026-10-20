"""S38-13d: the API knows the warm pool's RuntimeClass, from the same value the pool is made from."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
VALUE = "CHOREGOS_RUNNER_RUNTIME_CLASS"
BASE = ("global.rbac.jobRunner=true", "choregos-orchestrator.runner.namespacePattern=choregos")

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*overrides: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART), "--namespace", "choregos"]
    for override in (*overrides, *BASE):
        command += ["--set", override]
    return subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)


def _seen(*overrides: str) -> dict[str, set[str]]:
    """The value of the variable for each Deployment that sets it."""
    rendered = _render(*overrides)
    assert rendered.returncode == 0, rendered.stderr
    found: dict[str, set[str]] = {}
    for doc in yaml.safe_load_all(rendered.stdout):
        if not doc or doc.get("kind") != "Deployment":
            continue
        for container in doc["spec"]["template"]["spec"]["containers"]:
            for env in container.get("env", []):
                if env["name"] == VALUE:
                    found.setdefault(doc["metadata"]["name"], set()).add(env["value"])
    return found


def test_unset_means_the_cluster_default_for_the_api_and_every_worker() -> None:
    seen = _seen()
    assert "choregos-api" in seen, "the API is told, even when the answer is the cluster's default"
    assert any(name.startswith("choregos-orchestrator") for name in seen)
    assert set().union(*seen.values()) == {""}


def test_one_value_reaches_the_api_the_workers_and_the_template() -> None:
    seen = _seen("global.runner.runtimeClass=gvisor")
    assert seen["choregos-api"] == {"gvisor"}
    assert set().union(*seen.values()) == {"gvisor"}, "no pod has another answer"
    rendered = _render("global.runner.runtimeClass=gvisor").stdout
    template = next(d for d in yaml.safe_load_all(rendered) if d and d.get("kind") == "SandboxTemplate")
    assert template["spec"]["podTemplate"]["spec"]["runtimeClassName"] == "gvisor"


def test_the_orchestrator_may_repeat_the_value_not_contradict_it() -> None:
    assert _seen("global.runner.runtimeClass=gvisor", "choregos-orchestrator.runner.runtimeClass=gvisor")
    refused = _render("choregos-orchestrator.runner.runtimeClass=gvisor")
    assert refused.returncode != 0
    assert "global.runner.runtimeClass" in refused.stderr
