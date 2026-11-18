"""The egress gate in the pods the executors build (S80-07f, #1854): FIRST among the init containers,
before a hosted agent's sidecar (ADR 0080), run from the runner's image, its report mounted read-only in
the runner; and a pod it failed says so instead of "runner never started"."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.executor import egress_gate
from choregos_adapters.executor.agent_sandbox import AgentSandboxExecutor
from choregos_adapters.executor.k8s_job import AGENT_CONTAINER, KubernetesJobExecutor
from choregos_core.domain import AgentContainer, ExecRef, StageJobSpec
from choregos_core.egress_gate import GATE_CONTAINER, REPORT_ENV, EgressGate
from choregos_core.exit_codes import Exit

RUNNER = "harbor.example/choregos-runner:0.28.0"
HOSTED = "ghcr.io/acme/triager:1@sha256:" + "a" * 64


class _Client:
    """Records what is created; answers reads from `answers` (path -> body)."""

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.answers = answers or {}

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path, kwargs.get("json")))
        if path == "/version":
            return {"gitVersion": "v1.31.1"}
        return self.answers.get(path) if method == "GET" else None


def _spec(*, hosted: bool = False) -> StageJobSpec:
    return StageJobSpec(
        run_id="r-1",
        project_slug="demo",
        namespace="choregos",
        runner_image=RUNNER,
        api_url="http://choregos-api:8000",
        run_token="run-token",
        agent_container=AgentContainer(image=HOSTED, env={"PORT": "8088"}) if hosted else None,
    )


async def _pod(spec: StageJobSpec, **options: Any) -> dict[str, Any]:
    client = _Client()
    await KubernetesJobExecutor(client=client, **options).start(spec)  # type: ignore[arg-type]
    job = next(body for method, path, body in client.calls if method == "POST" and path.endswith("/jobs"))
    pod: dict[str, Any] = job["spec"]["template"]["spec"]
    return pod


async def test_the_gate_is_the_first_init_container_of_the_agent_pod() -> None:
    pod = await _pod(_spec(), egress_gate=EgressGate())

    first = pod["initContainers"][0]
    assert first["name"] == GATE_CONTAINER
    assert first["image"] == RUNNER, "no image of its own: the runner's, already pulled for the run"
    assert first["command"][:3] == [egress_gate.PYTHON, "-m", "choregos_core.egress_gate"]
    assert first["command"][3:] == EgressGate().args()
    assert "restartPolicy" not in first, "a plain init container: the pod waits for it to exit 0"
    assert "env" not in first and "envFrom" not in first, "the gate holds nothing of the run"


async def test_the_gate_precedes_the_hosted_agents_sidecar() -> None:
    """The hosted agent starts before the runner and runs its author's code from its first instant:
    the window of #1854 is open for it first."""
    pod = await _pod(_spec(hosted=True), egress_gate=EgressGate())

    assert [c["name"] for c in pod["initContainers"]] == [GATE_CONTAINER, AGENT_CONTAINER]
    assert pod["initContainers"][1]["restartPolicy"] == "Always"


async def test_the_runner_reads_the_gates_report_read_only() -> None:
    pod = await _pod(_spec(), egress_gate=EgressGate(target="10.9.9.9:443", deadline_s=12))

    runner = next(c for c in pod["containers"] if c["name"] == "runner")
    assert egress_gate.runner_mount() in runner["volumeMounts"]
    assert egress_gate.runner_mount()["readOnly"] is True
    assert {"name": REPORT_ENV, "value": "/egress-gate/report.json"} in runner["env"]
    assert egress_gate.volume() in pod["volumes"]
    assert pod["initContainers"][0]["command"][3:7] == ["--target", "10.9.9.9:443", "--deadline", "12"]


async def test_the_gate_runs_as_unprivileged_as_the_strict_policy_wants() -> None:
    pod = await _pod(_spec(), egress_gate=EgressGate())

    context = pod["initContainers"][0]["securityContext"]
    assert context["allowPrivilegeEscalation"] is False
    assert context["capabilities"] == {"drop": ["ALL"]}
    assert context["seccompProfile"] == {"type": "RuntimeDefault"}
    assert context["readOnlyRootFilesystem"] is True


@pytest.mark.parametrize("hosted", [False, True])
async def test_without_a_gate_the_pod_is_as_before(hosted: bool) -> None:
    pod = await _pod(_spec(hosted=hosted))

    assert GATE_CONTAINER not in [c["name"] for c in pod.get("initContainers", [])]
    assert all(v["name"] != egress_gate.GATE_VOLUME for v in pod["volumes"])


def test_the_deployment_turns_the_gate_on_for_k8s_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_adapters.registry import _REGISTRY

    config = {"kubernetes": {"base_url": "https://k8s.example", "token": "t", "verify": False}}
    monkeypatch.delenv("CHOREGOS_RUNNER_EGRESS_GATE", raising=False)
    assert _REGISTRY[("runtime", "k8s_job")](config).egress_gate is None
    monkeypatch.setenv("CHOREGOS_RUNNER_EGRESS_GATE", "true")
    monkeypatch.setenv("CHOREGOS_RUNNER_EGRESS_GATE_DEADLINE", "20")
    assert _REGISTRY[("runtime", "k8s_job")](config).egress_gate == EgressGate(deadline_s=20)


# ------------------------------------------------------------------ a pod the gate failed


def _failed_pod(code: int = int(Exit.EGRESS_OPEN)) -> dict[str, Any]:
    return {
        "metadata": {"name": "run-r-1-abcde", "creationTimestamp": "2026-11-18T10:00:00Z"},
        "status": {
            "phase": "Failed",
            "initContainerStatuses": [
                {"name": GATE_CONTAINER, "state": {"terminated": {"exitCode": code, "reason": "Error"}}}
            ],
            "containerStatuses": [{"name": "runner", "state": {"waiting": {"reason": "PodInitializing"}}}],
        },
    }


async def test_a_job_the_gate_failed_says_the_egress_policy_was_not_in_force() -> None:
    client = _Client(
        {
            "/apis/batch/v1/namespaces/choregos/jobs/run-r-1": {"status": {"failed": 1}},
            "/api/v1/namespaces/choregos/pods": {"items": [_failed_pod()]},
        }
    )
    executor = KubernetesJobExecutor(client=client)  # type: ignore[arg-type]
    ref = ExecRef(kind=executor.kind, name="run-r-1", namespace="choregos", run_id="r-1")

    status = await executor.status(ref)

    assert status.state == "failed"
    assert status.exit_code == Exit.EGRESS_OPEN
    assert status.reason == egress_gate.GATE_REASON
    assert "egress gate exited with 115" in status.message
    assert "phase: isolation" in status.message
    assert "never started" not in status.message


async def test_a_sandbox_the_gate_failed_says_so_too() -> None:
    client = _Client({"/api/v1/namespaces/choregos/pods/sb-1": _failed_pod()})
    executor = AgentSandboxExecutor(client=client)  # type: ignore[arg-type]

    code, cause = await executor._diagnostic("choregos", {"metadata": {"name": "sb-1"}})

    assert code == Exit.EGRESS_OPEN
    assert "egress gate exited with 115" in cause


def test_a_gate_that_exited_0_is_no_failure() -> None:
    assert egress_gate.gate_failure(_failed_pod(code=0)) is None
    assert egress_gate.gate_failure({"status": {}}) is None
    assert egress_gate.gate_failure(None) is None
