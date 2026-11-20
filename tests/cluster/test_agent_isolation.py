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

On kind (runc) it runs with a test image and the repository's `launcher.py`:
`TRANIBOR_CLUSTER_CONTEXT=kind-choregos uv run pytest tests/cluster/test_agent_isolation.py`.

**On a deployed platform (S65-04o)**, `TRANIBOR_ISOLATION_PLATFORM_NAMESPACE=<namespace>` runs the same
probe in that namespace, under its network policy, with what its warm pool's `SandboxTemplate` names:
the real runner image (whose own `launcher` is the agent's container), its RuntimeClass (`gvisor` on
the hardened clusters), its pull secrets and its egress gate. The warm-pool pod is the template's own
pod with the runner's command replaced by the probe; the Job is the one `k8s_job` renders with the same
settings. The probe then also asks, from the agent's container, whether the gate's target answers
directly and what the platform's egress proxy answers without credentials. It creates its own objects
in that namespace and deletes them; it touches nothing else. On the dev cluster:

    TRANIBOR_CLUSTER_CONTEXT=dev TRANIBOR_CLUSTER_ALLOW_SHARED=dev \
    TRANIBOR_ISOLATION_PLATFORM_NAMESPACE=choregos uv run pytest tests/cluster/test_agent_isolation.py

What this does NOT prove: a real agent (the probe is a shell), what a run's real CONNECT listener
carries (a run's journal does: `hosted.egress`), the agent-sandbox operator's suspension.
"""

from __future__ import annotations

import json
import os
import pathlib
import secrets
import sys
import time
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import kubectl_shared

pytestmark = pytest.mark.cluster

ROOT = pathlib.Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "packages" / "runner" / "src" / "tranibor_runner" / "launcher.py"
IMAGE = "python:3.12-slim"
PROBE_CODE = "import socket; socket.create_connection(('127.0.0.1', 8766))"
SECRET = "secret-run-token-01"  # a test value: the probe looks for it, it protects nothing
GATEWAY_KEY = "secret-gateway-key-01"  # idem, the run's gateway key in the Job's Secret
#: A deployed platform's namespace (S65-04o): the probe then runs there, as its warm pool's template says.
PLATFORM_NS = os.environ.get("TRANIBOR_ISOLATION_PLATFORM_NAMESPACE", "")
WARM_POOL_TEMPLATE = os.environ.get("TRANIBOR_ISOLATION_TEMPLATE", "choregos-runner")
#: Objects of this test, in a namespace it does not own: a suffix of their own.
SUFFIX = secrets.token_hex(3)
CONFIG = f"isolation-{SUFFIX}" if PLATFORM_NS else "isolation"
POD = f"isolation-{SUFFIX}" if PLATFORM_NS else "isolation"
RUN_ID = f"isolation-job-{SUFFIX}" if PLATFORM_NS else "isolation-job"

#: Asked from the agent's container on a platform: does the pod reach the gate's target directly, and
#: what does the egress proxy answer a CONNECT that carries no credentials? Prints `key=value` lines.
NETPROBE = r"""
import os, socket
def tcp(host, port):
    try:
        socket.create_connection((host, int(port)), timeout=3).close()
        return "open"
    except OSError:
        return "closed"
target = os.environ.get("EXTERNAL", "")
if target:
    host, port = target.rsplit(":", 1)
    print("external=" + tcp(host, port))
proxy = os.environ.get("PROXY", "")
if proxy:
    host, port = proxy.rsplit(":", 1)
    try:
        s = socket.create_connection((host, int(port)), timeout=5)
        s.sendall(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        print("proxy_without_credentials=" + s.recv(64).split(b" ")[1].decode())
    except (OSError, IndexError):
        print("proxy_without_credentials=unreachable")
"""

#: Looks for what the runner holds. The patterns use a character class (`[0]1`) so that the probe's
#: own command line, which `/proc` shows to the others, does not match itself.
PROBE = r"""
echo uid=$(id -u)
echo kernel=$(uname -r)
echo env_hits=$(cat /proc/[0-9]*/environ 2>/dev/null | tr '\0' '\n' | grep -c 'secret-run-token-[0]1')
E='/proc/[0-9]*/environ'
echo gateway_key_hits=$(cat $E 2>/dev/null | tr '\0' '\n' | grep -c 'secret-gateway-key-[0]1')
echo processes=$(ls -d /proc/[0-9]* | wc -l)
echo runner_processes=$(cat /proc/[0-9]*/cmdline 2>/dev/null | tr '\0' '\n' | grep -c 'runner-marker-[8]841')
if test -e /tmp/runner-only/secret; then echo runner_file=visible; else echo runner_file=absent; fi
echo agent > /workspace/from-agent
echo agent > /exchange/from-agent
echo wrote=ok
test -n "$NETPROBE" && "$PY" -c "$NETPROBE"
"""

RUNNER = f"""
import asyncio, json, os, subprocess, sys
try:  # the runner image's own launcher (S65-04o), the repository's copy otherwise
    from tranibor_runner import launcher as lc
except ImportError:
    sys.path.insert(0, "/cfg")
    import launcher as lc

PROBE = {PROBE!r}
NETPROBE = {NETPROBE!r}

def parse(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

def reset():
    for path in ("/workspace/from-agent", "/exchange/from-agent"):
        if os.path.exists(path):
            os.remove(path)

async def main():
    os.makedirs("/tmp/runner-only", exist_ok=True)
    open("/tmp/runner-only/secret", "w").write("the runner's own file")
    env = {{"PATH": os.environ["PATH"], "PY": sys.executable}}
    if os.environ.get("PROBE_NETWORK"):
        env.update(NETPROBE=NETPROBE, EXTERNAL=os.environ.get("PROBE_EXTERNAL", ""),
                   PROXY=os.environ.get("PROBE_PROXY", ""))
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


DROP = {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
CFG_MOUNT = {"name": "cfg", "mountPath": "/cfg"}


def _manifest(namespace: str) -> dict[str, Any]:
    mounts = [
        {"name": "workspace", "mountPath": "/workspace"},
        {"name": "exchange", "mountPath": "/exchange"},
    ]
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": POD, "namespace": namespace},
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
                        **DROP,
                        "runAsUser": 1001,
                        "runAsGroup": 1000,
                        "readOnlyRootFilesystem": True,
                    },
                    "volumeMounts": [*mounts, CFG_MOUNT],
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
                    "env": [{"name": "TRANIBOR_RUN_TOKEN", "value": SECRET}],
                    "securityContext": DROP,
                    "volumeMounts": [*mounts, CFG_MOUNT],
                }
            ],
            "volumes": [
                {"name": "workspace", "emptyDir": {}},
                {"name": "exchange", "emptyDir": {}},
                {"name": "cfg", "configMap": {"name": CONFIG}},
            ],
        },
    }


# S65-04o: a deployed platform ────────────────────────────────────────────────────────────────


def _template(namespace: str) -> dict[str, Any]:
    """The pod the platform's warm pool starts: its `SandboxTemplate`, as the cluster holds it."""
    found = json.loads(
        kubectl_shared("-n", namespace, "get", "sandboxtemplate", WARM_POOL_TEMPLATE, "-o", "json")
    )
    return dict(found["spec"]["podTemplate"])


def _runner_image(pod_spec: dict[str, Any]) -> str:
    return str(next(c for c in pod_spec["containers"] if c["name"] == "runner")["image"])


def _probe_env(pod_spec: dict[str, Any]) -> list[dict[str, str]]:
    """What the runner's container gets for the probe: the stand-ins for the run's credentials, and the
    two addresses the agent's container is asked about (the gate's target, the egress proxy)."""
    gate = next((c for c in pod_spec.get("initContainers", []) if c["name"] == "egress-gate"), None)
    args = list((gate or {}).get("args") or (gate or {}).get("command") or [])
    target = args[args.index("--target") + 1] if "--target" in args else ""
    proxy = os.environ.get("TRANIBOR_ISOLATION_EGRESS_PROXY", f"choregos-egress.{PLATFORM_NS}.svc:3128")
    return [
        {"name": "TRANIBOR_RUN_TOKEN", "value": SECRET},
        {"name": "TRANIBOR_RUN_GATEWAY_KEY_PROBE", "value": GATEWAY_KEY},
        {"name": "PROBE_NETWORK", "value": "1"},
        {"name": "PROBE_EXTERNAL", "value": target},
        {"name": "PROBE_PROXY", "value": proxy},
    ]


def _as_probe(runner: dict[str, Any], env: list[dict[str, str]]) -> None:
    """The runner's container runs the probe's script with the image's own interpreter, nothing else."""
    runner.pop("args", None)
    runner["command"] = ["/app/.venv/bin/python", "/cfg/runner.py", "runner-marker-8841"]
    names = {e["name"] for e in env}
    runner["env"] = [e for e in runner.get("env", []) if e["name"] not in names] + env
    runner.setdefault("volumeMounts", []).append(CFG_MOUNT)


def _platform_manifest(namespace: str) -> dict[str, Any]:
    """The warm pool's own pod (its template: image, RuntimeClass, egress gate, the `agent` sidecar with
    the image's launcher), the runner's command replaced by the probe. The run's volume claim the
    operator mounts at `/workspace` is an `emptyDir` here."""
    template = _template(namespace)
    pod_spec = template["spec"]
    (runner,) = [c for c in pod_spec["containers"] if c["name"] == "runner"]
    _as_probe(runner, _probe_env(pod_spec))
    if not any(v["name"] == "workspace" for v in pod_spec.get("volumes", [])):
        pod_spec.setdefault("volumes", []).append({"name": "workspace", "emptyDir": {}})
    pod_spec["volumes"].append({"name": "cfg", "configMap": {"name": CONFIG}})
    labels = {"choregos/probe": "s65-04o"}  # not the runner's labels: no pool or claim may take it
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": POD, "namespace": namespace, "labels": labels},
        "spec": pod_spec,
    }


@pytest.fixture(scope="module")
def where(request: pytest.FixtureRequest) -> Iterator[str]:
    """The namespace: a deployed platform's (its objects are this test's, deleted after), or a new one."""
    if not PLATFORM_NS:
        yield str(request.getfixturevalue("ns"))
        return
    try:
        yield PLATFORM_NS
    finally:
        for kind, name in (
            ("job", f"run-{RUN_ID}"),
            ("secret", f"run-{RUN_ID}"),
            ("pod", POD),
            ("configmap", CONFIG),
        ):
            kubectl_shared(
                "-n", PLATFORM_NS, "delete", kind, name, "--ignore-not-found", "--wait=false", check=False
            )


def _wait_for_pod(namespace: str) -> tuple[str, str]:
    deadline, phase = time.monotonic() + 300, ""
    while time.monotonic() < deadline:
        phase = kubectl_shared(
            "-n", namespace, "get", "pod", POD, "-o", "jsonpath={.status.phase}", check=False
        ).strip()
        if phase in {"Succeeded", "Failed"}:
            break
        time.sleep(3)
    return phase, kubectl_shared("-n", namespace, "logs", POD, "-c", "runner", check=False)


@pytest.fixture(scope="module")
def result(where: str) -> dict[str, Any]:
    kubectl_shared("-n", where, "create", "configmap", CONFIG, f"--from-file=launcher.py={LAUNCHER}")
    # The runner script goes in as a literal: the fixture builds it, the repository does not ship it.
    kubectl_shared(
        "-n",
        where,
        "patch",
        "configmap",
        CONFIG,
        "--type=merge",
        "-p",
        json.dumps({"data": {"runner.py": RUNNER}}),
    )
    manifest = _platform_manifest(where) if PLATFORM_NS else _manifest(where)
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(manifest))
    phase, logs = _wait_for_pod(where)
    agent_logs = kubectl_shared("-n", where, "logs", POD, "-c", "agent", check=False)
    assert phase == "Succeeded", (
        f"phase={phase or 'unknown'}\nrunner:\n{logs[-1500:]}\nagent:\n{agent_logs[-800:]}"
    )
    line = next((ln for ln in logs.splitlines() if ln.startswith("RESULT=")), "")
    assert line, f"the runner printed no result:\n{logs[-1500:]}"
    pod = json.loads(kubectl_shared("-n", where, "get", "pod", POD, "-o", "json"))
    found = dict(json.loads(line[len("RESULT=") :]))
    _report("warm pool pod" if PLATFORM_NS else "pod", pod, found)
    return {**found, "pod": pod}


def _report(what: str, pod: dict[str, Any], found: dict[str, Any]) -> None:
    """The evidence a STATUS row quotes: the probe prints only counts and words, never a value."""
    statuses = {
        s["name"]: s["state"].get("terminated", {}).get("exitCode")
        for s in pod["status"].get("initContainerStatuses", [])
    }
    print(
        f"\n{what}: runtimeClass={pod['spec'].get('runtimeClassName')} init={statuses} "
        f"image={_runner_image(pod['spec'])}\n  {json.dumps(found, sort_keys=True)}",
        file=sys.stderr,
    )


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
    assert result["container"]["gateway_key_hits"] == "0", result


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


@pytest.mark.skipif(not PLATFORM_NS, reason="a deployed platform's template, runtime and network policy")
def test_on_the_platform_the_pod_is_the_templates_under_its_runtime(result: dict[str, Any]) -> None:
    """The pod runs the platform's runner image under the template's RuntimeClass, its egress gate passed
    first, and the agent's container is the image's own launcher."""
    pod = result["pod"]
    template = _template(PLATFORM_NS)["spec"]
    assert pod["spec"].get("runtimeClassName") == template.get("runtimeClassName")
    assert _runner_image(pod["spec"]) == _runner_image(template)
    (agent,) = [c for c in pod["spec"]["initContainers"] if c["name"] == "agent"]
    assert agent["image"] == _runner_image(template) and "launcher" in (agent.get("args") or [])
    statuses = {s["name"]: s for s in pod["status"]["initContainerStatuses"]}
    if "egress-gate" in statuses:
        assert statuses["egress-gate"]["state"]["terminated"]["exitCode"] == 0, statuses
    if template.get("runtimeClassName") == "gvisor":
        assert "gvisor" in result["container"]["kernel"], result["container"]


@pytest.mark.skipif(not PLATFORM_NS, reason="a deployed platform's network policy")
def test_on_the_platform_the_agent_reaches_no_internet_but_through_the_runner(result: dict[str, Any]) -> None:
    """The pod's policy closes the gate's target to both containers, and the egress proxy refuses a
    tunnel that carries no run's credentials: the agent holds none, so its way out is the runner's
    CONNECT listener, which adds them."""
    agent = result["container"]
    if agent.get("external") is not None:
        assert agent["external"] == "closed", agent
    assert agent["proxy_without_credentials"] == "407", agent


# S65-04h: the `k8s_job` executor's own Job, on the same kubelet ───────────────────────────────


def _job_manifest(namespace: str) -> dict[str, Any]:
    """The Job `KubernetesJobExecutor` renders under `container`, with the test's images and scripts in
    place of the runner image's (the containers, their users, volumes, mounts, Secret reference and
    sidecar are the executor's own). No egress gate: kind enforces no NetworkPolicy to wait for.

    On a platform (S65-04o), with what its warm pool's template names: the runner image (and its own
    launcher for the agent), the RuntimeClass, the pull secrets and the egress gate."""
    from tranibor_adapters.executor.k8s_job import KubernetesJobExecutor
    from tranibor_core.domain import StageJobSpec
    from tranibor_core.egress_gate import EgressGate

    template = _template(namespace)["spec"] if PLATFORM_NS else {}
    gate_target = (
        next((e["value"] for e in _probe_env(template) if e["name"] == "PROBE_EXTERNAL"), "")
        if template
        else ""
    )
    spec = StageJobSpec(
        run_id=RUN_ID,
        project_slug="demo",
        namespace=namespace,
        runner_image=_runner_image(template) if template else IMAGE,
        api_url="http://choregos-api:8000",
        run_token=SECRET,
        agent_isolation="container",
        timeout_minutes=10,
        runtime_class=template.get("runtimeClassName") if template else None,
    )
    # The agent's limits the platform gives its runs (its template's agent container): a namespace under
    # a LimitRange refuses a pod beyond them (4Gi on the dev cluster this was proven on).
    agent_limits = next(
        (
            c.get("resources", {}).get("limits", {})
            for c in template.get("initContainers", [])
            if c["name"] == "agent"
        ),
        {},
    )
    executor = KubernetesJobExecutor(
        client=object(),  # type: ignore[arg-type]
        **({"cpu_limit": agent_limits["cpu"]} if "cpu" in agent_limits else {}),
        **({"memory_limit": agent_limits["memory"]} if "memory" in agent_limits else {}),
        image_pull_secrets=[s["name"] for s in template.get("imagePullSecrets", [])],
        egress_gate=EgressGate(target=gate_target) if gate_target else None,
    )
    job = executor._job(spec, f"run-{RUN_ID}")
    pod = job["spec"]["template"]["spec"]
    pod["volumes"].append({"name": "cfg", "configMap": {"name": CONFIG}})
    (runner,) = pod["containers"]
    if template:
        _as_probe(runner, [e for e in _probe_env(template) if e["name"] != "TRANIBOR_RUN_TOKEN"])
        return job
    pod.pop("serviceAccountName")  # the test namespace has no `choregos-runner` account
    (agent,) = [c for c in pod["initContainers"] if c["name"] == "agent"]
    agent.pop("args")
    agent["command"] = ["python", "/cfg/launcher.py", "8766"]
    agent["startupProbe"]["exec"]["command"] = ["python", "-c", PROBE_CODE]
    agent["volumeMounts"].append(CFG_MOUNT)
    runner.pop("args")
    runner["command"] = ["python", "/cfg/runner.py", "runner-marker-8841"]
    runner["volumeMounts"].append(CFG_MOUNT)
    return job


@pytest.fixture(scope="module")
def job_result(where: str, result: dict[str, Any]) -> dict[str, Any]:
    """After `result` (it made the ConfigMap): the run's Secret, as the executor writes it, then the Job."""
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": f"run-{RUN_ID}", "namespace": where},
        "stringData": {"token": SECRET, "gateway-key": GATEWAY_KEY},
    }
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(secret))
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(_job_manifest(where)))
    selector = f"choregos/run-id={RUN_ID}"
    deadline, done = time.monotonic() + 300, dict[str, Any]()
    while time.monotonic() < deadline:
        status = kubectl_shared("-n", where, "get", "job", f"run-{RUN_ID}", "-o", "json", check=False)
        done = (json.loads(status) if status.strip() else {}).get("status") or {}
        if done.get("succeeded") or done.get("failed"):
            break
        time.sleep(3)
    pod = json.loads(kubectl_shared("-n", where, "get", "pod", "-l", selector, "-o", "json"))["items"][0]
    name = pod["metadata"]["name"]
    logs = kubectl_shared("-n", where, "logs", name, "-c", "runner", check=False)
    assert done.get("succeeded") == 1, f"job status={done}\nrunner:\n{logs[-1500:]}"
    line = next((ln for ln in logs.splitlines() if ln.startswith("RESULT=")), "")
    assert line, f"the runner printed no result:\n{logs[-1500:]}"
    found = dict(json.loads(line[len("RESULT=") :]))
    _report("k8s_job pod", pod, found)
    agent_state = next(s for s in pod["status"].get("initContainerStatuses", []) if s["name"] == "agent")
    return {**found, "phase": pod["status"]["phase"], "agent": agent_state, "pod": pod}


@pytest.mark.capability("k8s_job", "separate_agent")
def test_the_job_completes_and_its_agent_sidecar_ends_with_it(job_result: dict[str, Any]) -> None:
    """The Job ends on the runner's exit alone: the native sidecar is stopped, not waited for."""
    assert job_result["phase"] == "Succeeded"
    assert "terminated" in job_result["agent"]["state"], job_result["agent"]


@pytest.mark.capability("k8s_job", "separate_agent")
def test_the_jobs_agent_reads_nothing_of_the_runner_and_shares_the_work(job_result: dict[str, Any]) -> None:
    """The run token and the gateway key reach the runner through the run's Secret (`secretKeyRef`), as
    the executor writes it; from the agent's container they are in no environment."""
    assert job_result["process"]["uid"] == "1000" and int(job_result["process"]["env_hits"]) >= 1
    assert int(job_result["process"]["gateway_key_hits"]) >= 1
    container = job_result["container"]
    assert container["uid"] == "1001"
    assert container["env_hits"] == "0" and container["gateway_key_hits"] == "0", job_result
    assert container["runner_processes"] == "0" and container["runner_file"] == "absent"
    assert all(value in ("agent", "ok") for value in job_result["shared"].values()), job_result


@pytest.mark.skipif(not PLATFORM_NS, reason="a deployed platform's runtime and network policy")
@pytest.mark.capability("k8s_job", "separate_agent")
def test_on_the_platform_the_jobs_pod_is_gated_and_closed_to_the_agent(job_result: dict[str, Any]) -> None:
    """The Job runs under the template's RuntimeClass, its egress gate passed first, and from its agent's
    container the internet is closed and the egress proxy refuses a tunnel without credentials."""
    pod = job_result["pod"]
    template = _template(PLATFORM_NS)["spec"]
    assert pod["spec"].get("runtimeClassName") == template.get("runtimeClassName")
    names = [c["name"] for c in pod["spec"]["initContainers"]]
    statuses = {s["name"]: s for s in pod["status"]["initContainerStatuses"]}
    if any(c["name"] == "egress-gate" for c in template.get("initContainers", [])):
        assert names[0] == "egress-gate", names
        assert statuses["egress-gate"]["state"]["terminated"]["exitCode"] == 0, statuses
    if template.get("runtimeClassName") == "gvisor":
        assert "gvisor" in job_result["container"]["kernel"], job_result["container"]
    agent = job_result["container"]
    if agent.get("external") is not None:
        assert agent["external"] == "closed", agent
    assert agent["proxy_without_credentials"] == "407", agent
