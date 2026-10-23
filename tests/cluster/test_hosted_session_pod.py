"""S41-24 — a hosted session's pod and a hosted workspace, on a real cluster.

S41-11 (`test_a_session_pod_answers_a_turn_through_the_runner`): the pod a session starts, with the
real `KubernetesJobExecutor` and the real runner image in `serve-session` mode, the reference agent
beside it as a native sidecar. A stand-in for the API and the model gateway
(`assets/session_stub_api.py`, in a pod of its own) hands the runner one turn. The agent answers it
through the runner's loopback: the model proxy (with the session's key, held by the runner only) and
the session's tools on `/mcp/choregos` (with the session's token). When the stand-in answers `410`
(the session ended) the runner exits 0, the sidecar is stopped by SIGTERM and the Job completes.
What it does not run: the workflow that decides *when* the session ends (the idle TTL, S41-11's
replayed history), nor the real API.

S41-18 (`test_the_hosted_container_changes_the_files_the_runner_copied`): the runner's own
`export_tree` fills the pod's shared `hosted-workspace` volume; a sidecar that runs as ANOTHER user
(10001, the image of a framework that does not use 1000) changes a file and adds one, thanks to the
pod's `fsGroup`; and it sees neither the runner's `/workspace`, nor a `.git`, nor a credential.

Both need the runner image (`docker build -f docker/runner.Dockerfile -t choregos-runner:s41-24 .`,
then `kind load docker-image`), named by `CHOREGOS_HOSTED_RUNNER_IMAGE`, and the reference image of
`test_hosted_native_sidecar.py`; without either they are skipped.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from choregos_adapters.executor.k8s_job import AGENT_CONTAINER, KubernetesJobExecutor
from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    HostedCall,
    ModelRef,
    PlaybookRef,
    ProjectRef,
    StageInput,
    TransitionRef,
    WorkItemRef,
)
from choregos_core.domain import AgentContainer, StageJobSpec
from choregos_core.hosted import NATIVE_SIDECARS_MIN, agent_container

from .conftest import kubectl_shared
from .test_hosted_native_sidecar import _cluster_version, _pod, _reference_image

pytestmark = pytest.mark.cluster

STUB = Path(__file__).parent / "assets" / "session_stub_api.py"
#: Built at run time: a literal that looks like a key would trip the secrets scan of the CI.
TOKEN = "session-token-" + secrets.token_hex(8)
KEY = "-".join(["sk", "session", secrets.token_hex(8)])
DIGEST = "@sha256:" + "a" * 64


def _runner_image() -> str:
    image = os.environ.get("CHOREGOS_HOSTED_RUNNER_IMAGE", "")
    if not image:
        pytest.skip("CHOREGOS_HOSTED_RUNNER_IMAGE unset: the runner's image is needed")
    return image


def _session_input(image: str) -> StageInput:
    """What `start_session_pod` builds (S41-11), for a pod without the platform."""
    return StageInput(
        run_id="session-s1",
        attempt=1,
        project=ProjectRef(slug="demo", org="varga"),
        work_item=WorkItemRef(key="session:s1", title="Session with triage"),
        transition=TransitionRef(id="session", role="session", **{"from": "open"}, to="open"),
        agent=AgentRef(backend="hosted"),
        model=ModelRef(litellm_model="platform/standard", base_url="http://s41-24-api/v1"),
        hosted=HostedCall(
            image=image.split("@", maxsplit=1)[0] + DIGEST, agent_slug="triage", agent_version=1
        ),
        gateway_key=None,
        budget=Budget(usd=1.0, max_turns=5, max_minutes=5),
        playbook=PlaybookRef(ref="session:triage@1"),
        callbacks=Callbacks(api_url="http://s41-24-api/api/v1/internal", run_token="REDACTED"),
    )


def _wait(ns_name: str, kind: str, name: str, condition: str) -> None:
    kubectl_shared("-n", ns_name, "wait", f"--for={condition}", f"{kind}/{name}", "--timeout=240s")


async def _until(read: Callable[[], str], states: set[str], timeout_s: float = 300) -> str:
    state, deadline = "", time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = read()
        if state in states:
            break
        await asyncio.sleep(3)
    return state


def _start_stub(ns_name: str, reference: str, stage_input: StageInput) -> None:
    """The API and model stand-in: a ConfigMap with its script, a pod, a Service."""
    config = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "s41-24-stub"},
        "data": {
            "api.py": STUB.read_text(),
            "stage_input.json": json.dumps(stage_input.model_dump(mode="json", by_alias=True)),
        },
    }
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "s41-24-api", "labels": {"app": "s41-24-api"}},
        "spec": {
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "stub",
                    "image": reference,
                    "command": ["python", "/stub/api.py"],
                    "env": [
                        {"name": "EXPECTED_TOKEN", "value": TOKEN},
                        {"name": "EXPECTED_KEY", "value": KEY},
                    ],
                    "readinessProbe": {"httpGet": {"path": "/healthz", "port": 8000}},
                    "volumeMounts": [{"name": "stub", "mountPath": "/stub"}],
                    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                }
            ],
            "volumes": [{"name": "stub", "configMap": {"name": "s41-24-stub"}}],
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "s41-24-api"},
        "spec": {"selector": {"app": "s41-24-api"}, "ports": [{"port": 80, "targetPort": 8000}]},
    }
    for manifest in (config, pod, service):
        kubectl_shared("-n", ns_name, "apply", "-f", "-", input_text=json.dumps(manifest))
    _wait(ns_name, "pod", "s41-24-api", "condition=Ready")


async def test_a_session_pod_answers_a_turn_through_the_runner(
    ns: str, executor: KubernetesJobExecutor
) -> None:
    reference, runner = _reference_image(), _runner_image()
    if await _cluster_version(executor) < NATIVE_SIDECARS_MIN:
        pytest.skip("the cluster is older than 1.29: no native sidecar")
    stage_input = _session_input(reference)
    _start_stub(ns, reference, stage_input)

    sidecar = agent_container(stage_input, mcp_servers=["choregos"])
    assert sidecar is not None
    spec = StageJobSpec(
        run_id="session-s1",
        project_slug="demo",
        namespace=ns,
        runner_image=runner,
        api_url="http://s41-24-api/api/v1/internal",
        run_token=TOKEN,
        gateway_key=KEY,
        stage_input=stage_input,
        timeout_minutes=10,
        cpu="100m",
        memory="256Mi",
        # The cluster has the image under the tag it was loaded with, not by digest.
        agent_container=sidecar.model_copy(update={"image": reference, "cpu": "250m", "memory": "256Mi"}),
        runner_command=["serve-session"],
    )
    ref = await executor.start(spec)
    state = await _until(lambda: _state(executor, ns, spec.run_id), {"succeeded", "failed"})
    log = kubectl_shared("-n", ns, "logs", "s41-24-api")
    events = [json.loads(line) for line in log.splitlines() if line.startswith("{")]
    assert state == "succeeded", f"the session's pod did not end cleanly: {state}\n{log[-1500:]}"

    pod = _pod(ns, spec.run_id)
    [declared] = pod["spec"]["initContainers"]
    assert declared["restartPolicy"] == "Always" and declared["name"] == AGENT_CONTAINER
    assert pod["spec"]["containers"][0]["args"] == ["serve-session"], "the runner serves the session"
    assert "valueFrom" not in json.dumps(declared) and "envFrom" not in declared, "no secret in the sidecar"
    assert KEY not in json.dumps(declared) and TOKEN not in json.dumps(declared)

    by_event = {e["event"]: e for e in events}
    assert by_event["input"]["authorized"] and by_event["turn.handed"]["authorized"]
    assert all(e["authorized"] for e in events if "authorized" in e), "the API saw the session's token only"
    calls = [e for e in events if e["event"] == "model.call"]
    assert [c["call"] for c in calls] == [1, 2] and all(c["keyed"] for c in calls), (
        "the agent's two model calls reached the gateway with the session's key, put there by the runner"
    )
    assert by_event["tools.call"]["arguments"] == {"query": "42"}, "the session's tool, through the runner"
    answer = by_event["turn.answered"]["answer"]
    assert answer["status"] == "answered", answer
    assert "Ticket 42 triaged: 3 tickets found." in answer["text"], "the agent's real answer, relayed"
    assert "session.closed" in by_event, "the 410 is what ended the pod"
    [agent] = pod["status"]["initContainerStatuses"]
    assert agent["state"]["terminated"]["exitCode"] == 0, "the sidecar was stopped by SIGTERM"

    # Stopping a pod that is already gone is not an error (the workflow's stop activity relies on it).
    await executor.cancel(ref)
    await executor.cancel(ref)


def _state(executor: KubernetesJobExecutor, ns_name: str, run_id: str) -> str:
    """The Job's phase read with kubectl (the executor's `status` is async)."""
    out = kubectl_shared(
        "-n",
        ns_name,
        "get",
        "pods",
        "-l",
        f"choregos/run-id={run_id}",
        "-o",
        "jsonpath={.items[0].status.phase}",
        check=False,
    ).strip()
    return {"Succeeded": "succeeded", "Failed": "failed"}.get(out, out or "pending")


SIDECAR_SCRIPT = (
    "trap 'exit 0' TERM; "
    "id -u; env | sort; ls -a /workspace; ls -ld /workspace /workspace/sub; "
    "for i in $(seq 1 120); do [ -f /workspace/a.txt ] && break; sleep 1; done; "
    "echo 'changed by the agent' >> /workspace/a.txt && echo CHANGED; "
    "echo new > /workspace/sub/new.txt && echo ADDED; "
    "test -e /workspace/.git && echo GIT_VISIBLE; test -e /runner-workspace && echo RUNNER_VOLUME_VISIBLE; "
    "sleep 3600 & wait"
)
RUNNER_SCRIPT = """
import os, pathlib, stat, sys, time
from choregos_runner.hosted_workspace import export_tree

tree = pathlib.Path('/tmp/tree')
(tree / 'sub').mkdir(parents=True)
(tree / '.git').mkdir()
(tree / 'a.txt').write_text('copied by the runner\\n')
(tree / 'sub' / 'b.txt').write_text('b\\n')
print('copied', export_tree(tree, pathlib.Path('/hosted-workspace')), flush=True)
deadline = time.monotonic() + 150
while not (pathlib.Path('/hosted-workspace/sub/new.txt')).exists() and time.monotonic() < deadline:
    time.sleep(1)
shared = pathlib.Path('/hosted-workspace')
print('a.txt:', (shared / 'a.txt').read_text().strip().replace('\\n', ' | '))
print('new.txt:', (shared / 'sub' / 'new.txt').read_text().strip())
print('git copied:', (shared / '.git').exists())
print('new.txt group:', os.stat(shared / 'sub' / 'new.txt').st_gid, flush=True)
"""


async def test_the_hosted_container_changes_the_files_the_runner_copied(
    ns: str,
    executor: KubernetesJobExecutor,
) -> None:
    reference, runner = _reference_image(), _runner_image()
    if await _cluster_version(executor) < NATIVE_SIDECARS_MIN:
        pytest.skip("the cluster is older than 1.29: no native sidecar")
    hosted = AgentContainer(
        image="registry.invalid/agent" + DIGEST,
        env={"PORT": "8088"},
        workspace="write",
        termination_grace_s=10,
    )
    spec = StageJobSpec(
        run_id=f"workspace-{secrets.token_hex(3)}",
        project_slug="demo",
        namespace=ns,
        runner_image=runner,
        api_url="http://192.0.2.1",
        run_token=TOKEN,
        timeout_minutes=5,
        cpu="100m",
        memory="256Mi",
        agent_container=hosted,
    )
    # The Job the real executor renders, with two things changed for the probe: what the two
    # containers execute, and the sidecar's user (not the runner's 1000). Volumes, mounts and the
    # pod's `fsGroup` are the executor's own.
    job = executor._job(spec, f"choregos-run-{spec.run_id}")
    pod_spec = job["spec"]["template"]["spec"]
    [sidecar] = pod_spec["initContainers"]
    sidecar.update(
        {"image": "curlimages/curl:8.11.1", "command": ["sh", "-c", SIDECAR_SCRIPT], "name": AGENT_CONTAINER}
    )
    sidecar["securityContext"]["runAsUser"] = 10001
    [runner_container] = pod_spec["containers"]
    runner_container["command"] = ["/app/.venv/bin/python", "-c", RUNNER_SCRIPT]
    runner_container["args"] = []
    runner_container["env"] = [e for e in runner_container["env"] if "valueFrom" not in e]
    assert reference  # the reference image is not used here: the probe needs a shell
    kubectl_shared("-n", ns, "apply", "-f", "-", input_text=json.dumps(job))

    state = await _until(lambda: _state(executor, ns, spec.run_id), {"succeeded", "failed"})
    pod = _pod(ns, spec.run_id)["metadata"]["name"]
    runner_log = kubectl_shared("-n", ns, "logs", pod, "-c", "runner", check=False)
    agent_log = kubectl_shared("-n", ns, "logs", pod, "-c", AGENT_CONTAINER, check=False)
    assert "CHANGED" in agent_log, f"the sidecar could not write in the shared volume:\n{agent_log}"
    assert state == "succeeded", f"the probe pod did not end cleanly: {state}\n{runner_log}\n{agent_log}"

    assert pod_spec["securityContext"].get("fsGroup") == 1000
    assert agent_log.splitlines()[0] == "10001", "the sidecar is not the runner's user"
    assert "CHANGED" in agent_log and "ADDED" in agent_log, f"the sidecar could not write:\n{agent_log}"
    assert "a.txt: copied by the runner | changed by the agent" in runner_log, runner_log
    assert "new.txt: new" in runner_log and "new.txt group: 1000" in runner_log, runner_log
    assert "git copied: False" in runner_log, "the runner's `.git` was not copied into the shared volume"
    assert "GIT_VISIBLE" not in agent_log and "RUNNER_VOLUME_VISIBLE" not in agent_log
    for secret_name in ("CHOREGOS_RUN_TOKEN", "CHOREGOS_RUN_GATEWAY_KEY", "GIT_", "GITHUB_", "TOKEN"):
        assert not any(
            line.startswith(secret_name) or f"_{secret_name}" in line.split("=")[0]
            for line in agent_log.splitlines()
            if "=" in line
        ), f"{secret_name} is in the sidecar's environment"
    declared: dict[str, Any] = _pod(ns, spec.run_id)["spec"]["initContainers"][0]
    assert {m["name"] for m in declared["volumeMounts"]} == {"hosted-workspace"}, "only the shared tree"
