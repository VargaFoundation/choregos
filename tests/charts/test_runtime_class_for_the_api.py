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


def test_never_set_leaves_the_api_without_a_value_so_it_judges_nothing() -> None:
    seen = _seen()
    assert "choregos-api" not in seen, "an API told nothing reads the pool's class as unknown"
    assert set().union(*seen.values()) == {""}, "the workers keep the previous behavior"


def test_the_sub_chart_value_alone_still_reaches_the_pool_but_not_the_api() -> None:
    seen = _seen("choregos-orchestrator.runner.runtimeClass=gvisor")
    assert "choregos-api" not in seen, "else the API would read a gvisor pool as the cluster default"
    assert set().union(*seen.values()) == {"gvisor"}
    rendered = _render("choregos-orchestrator.runner.runtimeClass=gvisor").stdout
    template = next(d for d in yaml.safe_load_all(rendered) if d and d.get("kind") == "SandboxTemplate")
    assert template["spec"]["podTemplate"]["spec"]["runtimeClassName"] == "gvisor"


def test_an_explicit_empty_global_means_the_cluster_default_for_the_api() -> None:
    seen = _seen("global.runner.runtimeClass=")
    assert seen["choregos-api"] == {""}
    assert set().union(*seen.values()) == {""}


def test_one_global_value_reaches_the_api_the_workers_and_the_template() -> None:
    seen = _seen("global.runner.runtimeClass=gvisor")
    assert seen["choregos-api"] == {"gvisor"}
    assert set().union(*seen.values()) == {"gvisor"}, "no pod has another answer"
    rendered = _render("global.runner.runtimeClass=gvisor").stdout
    template = next(d for d in yaml.safe_load_all(rendered) if d and d.get("kind") == "SandboxTemplate")
    assert template["spec"]["podTemplate"]["spec"]["runtimeClassName"] == "gvisor"


def test_both_may_agree_but_not_contradict() -> None:
    assert _seen("global.runner.runtimeClass=gvisor", "choregos-orchestrator.runner.runtimeClass=gvisor")
    refused = _render("global.runner.runtimeClass=kata", "choregos-orchestrator.runner.runtimeClass=gvisor")
    assert refused.returncode != 0
    assert "global.runner.runtimeClass" in refused.stderr
