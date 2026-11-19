"""S80-07f, #1854: the chart puts the egress gate first in every agent pod it describes, and tells the
`k8s_job` executor to do the same.

- The `agent_sandbox` template's FIRST init container is the gate, before the ADR 0146 agent container,
  and it is the very container the executor builds for `k8s_job`
  (`tranibor_adapters.executor.egress_gate.init_container`): one gate, not two that drift.
- The executor workers receive `CHOREGOS_RUNNER_EGRESS_GATE`: on by default, on in the single-node
  profile (whose guard refuses it off), off on a laptop's `values/local.yaml` only.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml
from tranibor_adapters.executor import egress_gate
from tranibor_core.egress_gate import GATE_CONTAINER, REPORT_ENV, EgressGate, gate_from_env

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
VALUES = CHART / "values"
SITE = [
    "--set",
    "backup.target.s3.url=s3://backups/choregos",
    "--set",
    "backup.target.s3.endpoint=http://s3:8333",
]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART), "-n", "choregos", *arguments]
    return subprocess.run(command, capture_output=True, text=True, check=check, timeout=120)


def _documents(*arguments: str) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(_render(*arguments).stdout) if d]


def _template_pod(*arguments: str) -> dict[str, Any]:
    template = next(d for d in _documents(*arguments) if d["kind"] == "SandboxTemplate")
    pod: dict[str, Any] = template["spec"]["podTemplate"]["spec"]
    return pod


def _executor_env(*arguments: str) -> dict[str, str]:
    deployment = next(
        d
        for d in _documents(*arguments)
        if d["kind"] == "Deployment" and d["metadata"]["name"] == "choregos-orchestrator-executor"
    )
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    return {e["name"]: e.get("value", "") for e in container["env"]}


@pytest.mark.parametrize("isolation", ["process", "container"])
def test_the_sandbox_templates_first_init_container_is_the_gate(isolation: str) -> None:
    pod = _template_pod("--set", f"global.runner.agentIsolation={isolation}")

    names = [c["name"] for c in pod["initContainers"]]
    assert names[0] == GATE_CONTAINER
    assert names == ([GATE_CONTAINER, "agent"] if isolation == "container" else [GATE_CONTAINER])
    runner = next(c for c in pod["containers"] if c["name"] == "runner")
    assert pod["initContainers"][0]["image"] == runner["image"], "the runner's image: nothing more to pull"


def test_the_chart_and_the_executor_build_the_same_gate() -> None:
    pod = _template_pod(
        "--set",
        "choregos-orchestrator.runner.egressGate.target=203.0.113.9:8443",
        "--set",
        "choregos-orchestrator.runner.egressGate.deadlineSeconds=12",
    )
    rendered = pod["initContainers"][0]
    built = egress_gate.init_container(
        rendered["image"], EgressGate(target="203.0.113.9:8443", deadline_s=12)
    )

    for key in ("name", "command", "resources", "securityContext", "volumeMounts"):
        assert rendered[key] == built[key], key
    runner = next(c for c in pod["containers"] if c["name"] == "runner")
    assert egress_gate.runner_mount() in runner["volumeMounts"]
    assert egress_gate.runner_env() in runner["env"]
    assert egress_gate.volume() in pod["volumes"]


def test_the_gate_can_be_turned_off_and_then_leaves_no_trace() -> None:
    pod = _template_pod("--set", "choregos-orchestrator.runner.egressGate.enabled=false")

    assert "initContainers" not in pod
    assert "volumes" not in pod
    runner = next(c for c in pod["containers"] if c["name"] == "runner")
    assert REPORT_ENV not in {e["name"] for e in runner.get("env", [])}
    assert (
        _executor_env("--set", "choregos-orchestrator.runner.egressGate.enabled=false")[
            "CHOREGOS_RUNNER_EGRESS_GATE"
        ]
        == "false"
    )


def test_the_executor_is_told_to_gate_its_pods_by_default() -> None:
    env = _executor_env()

    assert env["CHOREGOS_RUNNER_EGRESS_GATE"] == "true"
    assert env["CHOREGOS_RUNNER_EGRESS_GATE_TARGET"] == "1.1.1.1:443"
    assert env["CHOREGOS_RUNNER_EGRESS_GATE_DEADLINE"] == "30"
    assert gate_from_env(env) == EgressGate()


def test_the_single_node_profile_gates_its_agent_pods() -> None:
    env = _executor_env("-f", str(VALUES / "single-node.yaml"), *SITE)

    assert env["CHOREGOS_RUNNER_EGRESS_GATE"] == "true"


def test_the_single_node_guard_refuses_the_gate_off() -> None:
    result = _render(
        "-f",
        str(VALUES / "single-node.yaml"),
        *SITE,
        "--set",
        "choregos-orchestrator.runner.egressGate.enabled=false",
        check=False,
    )

    assert result.returncode != 0
    assert "egressGate.enabled=true" in result.stderr


def test_a_laptop_bench_without_network_policies_turns_it_off() -> None:
    """`values/local.yaml` renders no NetworkPolicy: a gate there would refuse every agent pod."""
    assert _executor_env("-f", str(VALUES / "local.yaml"))["CHOREGOS_RUNNER_EGRESS_GATE"] == "false"
