"""ADR 0146 — the agent's container cannot read the runner's secrets, on a real kubelet.

The pod is the shape the chart renders for `global.runner.agentIsolation: container`: a `runner`
container (uid 1000, the run's secret in its environment, files of its own) and a native sidecar
`agent` (uid 1001, no secret) running `launcher.py`, the file the runner image ships, copied here
from the repository. The `runner` container plays the runner's part: it asks the launcher to run
a probe, then runs THE SAME probe as its own child, which is what the platform did until 0.27.

What the probe looks for is what the audit of 2026-11-14 read from the agent: the run token in
`/proc/<runner>/environ`, the runner's process, a file the runner keeps. As the runner's child it
finds all three; through the launcher it finds none. The shared volumes still work both ways
(the runner reads and appends to what the agent wrote: umask and `fsGroup`).

What this does NOT prove: gVisor (the kind nodes run runc; the container boundary is the same
Linux namespaces, gVisor adds its own kernel), the real runner image and its checks, a real agent.
Run it with `CHOREGOS_CLUSTER_CONTEXT=kind-choregos uv run pytest tests/cluster/test_agent_isolation.py`.
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any

import pytest

from .conftest import kubectl_shared

pytestmark = pytest.mark.cluster

ROOT = pathlib.Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "packages" / "runner" / "src" / "tranibor_runner" / "launcher.py"
IMAGE = "python:3.12-slim"
PROBE_CODE = "import socket; socket.create_connection(('127.0.0.1', 8766))"
SECRET = "secret-run-token-01"  # a test value: the probe looks for it, it protects nothing

#: Looks for what the runner holds. The patterns use a character class (`[0]1`) so that the probe's
#: own command line, which `/proc` shows to the others, does not match itself.
PROBE = r"""
echo uid=$(id -u)
echo env_hits=$(cat /proc/[0-9]*/environ 2>/dev/null | tr '\0' '\n' | grep -c 'secret-run-token-[0]1')
echo runner_processes=$(cat /proc/[0-9]*/cmdline 2>/dev/null | tr '\0' '\n' | grep -c 'runner-marker-[8]841')
if test -e /tmp/runner-only/secret; then echo runner_file=visible; else echo runner_file=absent; fi
echo agent > /workspace/from-agent
echo agent > /exchange/from-agent
echo wrote=ok
"""

RUNNER = f"""
import asyncio, json, os, subprocess, sys
sys.path.insert(0, "/cfg")
import launcher as lc

PROBE = {PROBE!r}

def parse(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

def reset():
    for path in ("/workspace/from-agent", "/exchange/from-agent"):
        if os.path.exists(path):
            os.remove(path)

async def main():
    os.makedirs("/tmp/runner-only", exist_ok=True)
    open("/tmp/runner-only/secret", "w").write("the runner's own file")
    env = {{"PATH": os.environ["PATH"]}}
    # The control: the agent as the runner's child, as in `process` mode.
    local = parse(subprocess.run(["sh", "-c", PROBE], capture_output=True, text=True, env=env).stdout)
    reset()
    launcher = lc.Launcher()
    assert await launcher.wait_ready(90), "no launcher"
    process = await launcher.spawn(PROBE, cwd="/workspace", env=env)
    out, err = await process.communicate()
    container = parse(out.decode())
    shared = {{}}
    for path in ("/workspace/from-agent", "/exchange/from-agent"):
        try:
            shared[path] = open(path).read().strip()
            with open(path, "a") as handle:  # the runner writes what the agent wrote
                handle.write("runner\\n")
            shared[path + ":append"] = "ok"
        except OSError as exc:
            shared[path] = "ERROR " + str(exc)
    print("RESULT=" + json.dumps({{"process": local, "container": container, "shared": shared,
                                   "launcher_stderr": err.decode()[-300:]}}), flush=True)

asyncio.run(main())
"""


def _manifest(namespace: str) -> dict[str, Any]:
    drop = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
    mounts = [
        {"name": "workspace", "mountPath": "/workspace"},
        {"name": "exchange", "mountPath": "/exchange"},
    ]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "isolation", "namespace": namespace},
        "spec": {
            "restartPolicy": "Never",
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "initContainers": [
                {
                    "name": "agent",
                    "image": IMAGE,
                    "restartPolicy": "Always",
                    "command": ["python", "/cfg/launcher.py", "8766"],
                    "securityContext": {
                        **drop,
                        "runAsUser": 1001,
                        "runAsGroup": 1000,
                        "readOnlyRootFilesystem": True,
                    },
                    "volumeMounts": [*mounts, {"name": "cfg", "mountPath": "/cfg"}],
                    "startupProbe": {
                        "exec": {"command": ["python", "-c", PROBE_CODE]},
                        "periodSeconds": 1,
                        "failureThreshold": 60,
                    },
                }
            ],
            "containers": [
                {
                    "name": "runner",
                    "image": IMAGE,
                    "command": ["python", "/cfg/runner.py", "runner-marker-8841"],
                    "env": [{"name": "CHOREGOS_RUN_TOKEN", "value": SECRET}],
                    "securityContext": drop,
                    "volumeMounts": [*mounts, {"name": "cfg", "mountPath": "/cfg"}],
                }
            ],
            "volumes": [
                {"name": "workspace", "emptyDir": {}},
                {"name": "exchange", "emptyDir": {}},
                {"name": "cfg", "configMap": {"name": "isolation"}},
            ],
        },
    }


@pytest.fixture(scope="module")
def result(ns: str) -> dict[str, Any]:
    kubectl_shared("-n", ns, "create", "configmap", "isolation", f"--from-file=launcher.py={LAUNCHER}")
    # The runner script goes in as a literal: the fixture builds it, the repository does not ship it.
    kubectl_shared(
        "-n",
        ns,
        "patch",
        "configmap",
        "isolation",
        "--type=merge",
        "-p",
        json.dumps({"data": {"runner.py": RUNNER}}),
    )
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(_manifest(ns)))
    deadline, phase = time.monotonic() + 240, ""
    while time.monotonic() < deadline:
        phase = kubectl_shared(
            "-n", ns, "get", "pod", "isolation", "-o", "jsonpath={.status.phase}", check=False
        ).strip()
        if phase in {"Succeeded", "Failed"}:
            break
        time.sleep(3)
    logs = kubectl_shared("-n", ns, "logs", "isolation", "-c", "runner", check=False)
    agent_logs = kubectl_shared("-n", ns, "logs", "isolation", "-c", "agent", check=False)
    assert phase == "Succeeded", (
        f"phase={phase or 'unknown'}\nrunner:\n{logs[-1500:]}\nagent:\n{agent_logs[-800:]}"
    )
    line = next((ln for ln in logs.splitlines() if ln.startswith("RESULT=")), "")
    assert line, f"the runner printed no result:\n{logs[-1500:]}"
    return dict(json.loads(line[len("RESULT=") :]))


def test_the_control_sees_everything_as_the_runners_child(result: dict[str, Any]) -> None:
    """If this fails, the probe proves nothing: it must be able to find what it looks for."""
    control = result["process"]
    assert control["uid"] == "1000"
    assert int(control["env_hits"]) >= 1, "the runner's environment is readable by its own child"
    assert int(control["runner_processes"]) >= 1
    assert control["runner_file"] == "visible"


def test_the_agent_container_runs_as_another_uid(result: dict[str, Any]) -> None:
    assert result["container"]["uid"] == "1001"


def test_the_agent_container_cannot_read_the_runners_environment(result: dict[str, Any]) -> None:
    assert result["container"]["env_hits"] == "0", result


def test_the_agent_container_does_not_see_the_runners_process_nor_files(result: dict[str, Any]) -> None:
    assert result["container"]["runner_processes"] == "0", result
    assert result["container"]["runner_file"] == "absent", result


def test_the_shared_volumes_still_carry_the_work_both_ways(result: dict[str, Any]) -> None:
    """The agent writes, the runner reads and appends: `fsGroup` and the launcher's umask."""
    assert result["container"]["wrote"] == "ok", result
    assert result["shared"] == {
        "/workspace/from-agent": "agent",
        "/workspace/from-agent:append": "ok",
        "/exchange/from-agent": "agent",
        "/exchange/from-agent:append": "ok",
    }, result


# S65-04h: the `k8s_job` executor's own Job, on the same kubelet ───────────────────────────────


def _job_manifest(namespace: str) -> dict[str, Any]:
    """The Job `KubernetesJobExecutor` renders under `container`, with the test's images and scripts in
    place of the runner image's (the containers, their users, volumes, mounts, Secret reference and
    sidecar are the executor's own). No egress gate: kind enforces no NetworkPolicy to wait for."""
    from tranibor_adapters.executor.k8s_job import KubernetesJobExecutor
    from tranibor_core.domain import StageJobSpec

    spec = StageJobSpec(
        run_id="isolation-job",
        project_slug="demo",
        namespace=namespace,
        runner_image=IMAGE,
        api_url="http://choregos-api:8000",
        run_token=SECRET,
        agent_isolation="container",
        timeout_minutes=10,
    )
    executor = KubernetesJobExecutor(client=object())  # type: ignore[arg-type]
    job = executor._job(spec, "run-isolation-job")
    pod = job["spec"]["template"]["spec"]
    pod.pop("serviceAccountName")  # the test namespace has no `choregos-runner` account
    cfg = {"name": "cfg", "mountPath": "/cfg"}
    pod["volumes"].append({"name": "cfg", "configMap": {"name": "isolation"}})
    (agent,) = [c for c in pod["initContainers"] if c["name"] == "agent"]
    agent.pop("args")
    agent["command"] = ["python", "/cfg/launcher.py", "8766"]
    agent["startupProbe"]["exec"]["command"] = ["python", "-c", PROBE_CODE]
    agent["volumeMounts"].append(cfg)
    (runner,) = pod["containers"]
    runner.pop("args")
    runner["command"] = ["python", "/cfg/runner.py", "runner-marker-8841"]
    runner["volumeMounts"].append(cfg)
    return job


@pytest.fixture(scope="module")
def job_result(ns: str, result: dict[str, Any]) -> dict[str, Any]:
    """After `result` (it made the ConfigMap): the run's Secret, as the executor writes it, then the Job."""
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "run-isolation-job", "namespace": ns},
        "stringData": {"token": SECRET},
    }
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(secret))
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(_job_manifest(ns)))
    selector = "choregos/run-id=isolation-job"
    deadline, done = time.monotonic() + 300, {}
    while time.monotonic() < deadline:
        status = kubectl_shared("-n", ns, "get", "job", "run-isolation-job", "-o", "json", check=False)
        done = (json.loads(status) if status.strip() else {}).get("status") or {}
        if done.get("succeeded") or done.get("failed"):
            break
        time.sleep(3)
    pod = json.loads(kubectl_shared("-n", ns, "get", "pod", "-l", selector, "-o", "json"))["items"][0]
    name = pod["metadata"]["name"]
    logs = kubectl_shared("-n", ns, "logs", name, "-c", "runner", check=False)
    assert done.get("succeeded") == 1, f"job status={done}\nrunner:\n{logs[-1500:]}"
    line = next((ln for ln in logs.splitlines() if ln.startswith("RESULT=")), "")
    assert line, f"the runner printed no result:\n{logs[-1500:]}"
    agent_state = next(s for s in pod["status"].get("initContainerStatuses", []) if s["name"] == "agent")
    return {**json.loads(line[len("RESULT=") :]), "phase": pod["status"]["phase"], "agent": agent_state}


@pytest.mark.capability("k8s_job", "separate_agent")
def test_the_job_completes_and_its_agent_sidecar_ends_with_it(job_result: dict[str, Any]) -> None:
    """The Job ends on the runner's exit alone: the native sidecar is stopped, not waited for."""
    assert job_result["phase"] == "Succeeded"
    assert "terminated" in job_result["agent"]["state"], job_result["agent"]


@pytest.mark.capability("k8s_job", "separate_agent")
def test_the_jobs_agent_reads_nothing_of_the_runner_and_shares_the_work(job_result: dict[str, Any]) -> None:
    """The run token reaches the runner through the run's Secret (`secretKeyRef`), as the executor
    writes it; from the agent's container it is in no environment."""
    assert job_result["process"]["uid"] == "1000" and int(job_result["process"]["env_hits"]) >= 1
    container = job_result["container"]
    assert container["uid"] == "1001"
    assert container["env_hits"] == "0", job_result
    assert container["runner_processes"] == "0" and container["runner_file"] == "absent"
    assert all(value in ("agent", "ok") for value in job_result["shared"].values()), job_result
