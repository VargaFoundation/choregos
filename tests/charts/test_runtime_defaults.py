"""ADR 0072: a default install runs agents in agent-sandbox sandboxes.

What is proved here, without a cluster, by rendering the chart:

- the defaults: SandboxTemplate rendered, workers on `agent_sandbox`, runs in the release namespace;
- the API receives the executor too (it printed "tekton" while the workers ran `k8s_job`);
- without the operator the render fails and names the operator and the way out;
- Tekton's objects (ClusterRole on tekton.dev, the Task ConfigMap) exist only for Tekton;
- the local bench keeps `k8s_job` by writing it;
- the orchestrator cannot disagree with the global setting.

Not proved: a run on a real cluster with these defaults.
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

needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "choregos", str(CHART), "--namespace", "choregos", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=120,
    )


def _docs(*args: str) -> dict[str, dict[str, Any]]:
    return {
        f"{doc['kind']}/{doc['metadata']['name']}": doc
        for doc in yaml.safe_load_all(_render(*args).stdout)
        if doc and doc.get("kind")
    }


def _env(deployment: dict[str, Any], container: str | None = None) -> dict[str, str]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    chosen = containers[0] if container is None else next(c for c in containers if c["name"] == container)
    return {e["name"]: e.get("value", "") for e in chosen["env"]}


@needs_helm
def test_a_default_install_runs_agents_in_sandboxes() -> None:
    objects = _docs()
    assert "SandboxTemplate/choregos-runner" in objects
    worker = _env(objects["Deployment/choregos-orchestrator-executor"])
    assert worker["CHOREGOS_EXECUTOR_KIND"] == "agent_sandbox"
    assert worker["CHOREGOS_RUNNER_NAMESPACE_PATTERN"] == "choregos", "the release namespace"


@needs_helm
def test_the_api_knows_the_executor() -> None:
    objects = _docs("--set", "global.runner.allowedExecutors={agent_sandbox,k8s_job}")
    api = _env(objects["Deployment/choregos-api"], "api")
    assert api["CHOREGOS_EXECUTOR_KIND"] == "agent_sandbox"
    assert api["CHOREGOS_EXECUTORS_ALLOWED"] == "agent_sandbox,k8s_job"


@needs_helm
@pytest.mark.no_operator
def test_without_the_operator_the_render_fails_and_names_it() -> None:
    result = _render(check=False)
    assert result.returncode != 0
    assert "agent-sandbox operator" in result.stderr and "k8s_job" in result.stderr


@needs_helm
@pytest.mark.no_operator
def test_the_operator_check_can_be_skipped_for_an_offline_rendering() -> None:
    assert "SandboxTemplate/choregos-runner" in _docs(
        "--set", "choregos-orchestrator.runner.skipOperatorCheck=true"
    )


@needs_helm
@pytest.mark.no_operator
def test_other_executors_need_no_operator() -> None:
    objects = _docs("--set", "global.runner.executor=k8s_job")
    assert "SandboxTemplate/choregos-runner" not in objects
    assert _env(objects["Deployment/choregos-api"], "api")["CHOREGOS_EXECUTOR_KIND"] == "k8s_job"
    assert _env(objects["Deployment/choregos-orchestrator-executor"])[
        "CHOREGOS_RUNNER_NAMESPACE_PATTERN"
    ] == ("proj-{slug}-runners")


@needs_helm
def test_tekton_objects_only_for_tekton() -> None:
    default = _render().stdout
    assert "tekton.dev" not in default and "choregos-agent-stage-task" not in default
    tekton = _render("--set", "global.runner.executor=tekton").stdout
    assert "tekton.dev" in tekton and "choregos-agent-stage-task" in tekton


@needs_helm
def test_local_values_keep_k8s_job() -> None:
    objects = _docs("-f", str(CHART / "values" / "local.yaml"))
    assert "SandboxTemplate/choregos-runner" not in objects
    assert _env(objects["Deployment/choregos-api"], "api")["CHOREGOS_EXECUTOR_KIND"] == "k8s_job"


@needs_helm
def test_the_orchestrator_cannot_disagree_with_the_global_setting() -> None:
    result = _render("--set", "choregos-orchestrator.runner.executor=k8s_job", check=False)
    assert result.returncode != 0 and "global.runner.executor" in result.stderr


@needs_helm
def test_prod_values_write_the_executor_and_warm_the_pool() -> None:
    objects = _docs("-f", str(CHART / "values" / "prod.yaml"))
    assert objects["SandboxWarmPool/choregos-runner"]["spec"]["replicas"] == 2
