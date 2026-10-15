"""S35-02 — a warm sandbox from the chart's template, on a real agent-sandbox operator.

What only a cluster shows: the pod the CONTROLLER makes from the chart's template carries no
credential; a claim adopts that warm sandbox rather than making another; a pod of the namespace
that is not the orchestrator's executor cannot reach its assignment port (the CNI enforces the
chart's NetworkPolicy); the runner in it accepts one run and refuses a second; deleting the
claim deletes the sandbox, and the pool replaces it with a NEW one — a sandbox is never handed
out twice.

It needs a runner image that has `await-assignment` (this release or later), named by
`CHOREGOS_SANDBOX_RUNNER_IMAGE`, and the operator; without them it is skipped and says why.
The executor's push itself comes from the orchestrator's pod, inside the cluster; here a probe
pod in the test namespace plays that part.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters.sealing import key_from_log, new_key, seal

from .conftest import kubectl_shared
from .test_agent_sandbox import _condition, _get, _wait, operator_installed

pytestmark = pytest.mark.cluster

CHART_SANDBOX = "charts/choregos-orchestrator/templates/sandbox.yaml"


@pytest.fixture(scope="module")
def ns() -> Iterator[str]:
    operator_installed()
    name = f"choregos-sandbox-exec-{secrets.token_hex(3)}"
    kubectl_shared("create", "namespace", name)
    try:
        yield name
    finally:
        kubectl_shared("delete", "namespace", name, "--wait=false", "--ignore-not-found", check=False)


def _runner_image() -> str:
    image = os.environ.get("CHOREGOS_SANDBOX_RUNNER_IMAGE", "")
    if not image:
        pytest.skip("CHOREGOS_SANDBOX_RUNNER_IMAGE unset: a runner image with `await-assignment` is needed")
    if shutil.which("helm") is None:
        pytest.skip("helm absent")
    return image


def _render(ns: str, image: str) -> str:
    registry, _, rest = image.partition("/")
    repository, _, tag = rest.rpartition(":")
    return subprocess.run(
        [
            "helm", "template", "choregos", "charts/choregos", "--namespace", ns,
            "--set", "choregos-orchestrator.runner.executor=agent_sandbox",
            "--set", "choregos-orchestrator.runner.skipOperatorCheck=true",
            "--set", f"choregos-orchestrator.runner.namespacePattern={ns}",
            "--set", "choregos-orchestrator.runner.warmPool.size=1",
            "--set", f"global.imageRegistry={registry}",
            "--set", f"choregos-orchestrator.runner.image={repository}",
            "--set", f"choregos-orchestrator.runner.tag={tag}",
            "--show-only", CHART_SANDBOX,
        ],
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout  # fmt: skip


def _sandboxes(ns: str) -> list[dict[str, Any]]:
    out = kubectl_shared("-n", ns, "get", "sandboxes", "-o", "json")
    return list(json.loads(out).get("items", []))


def _ready(sandbox: dict[str, Any]) -> bool:
    return _condition(sandbox, "Ready").get("status") == "True"


#: The labels of the orchestrator's `executor` workers: the only pods the assignment port admits.
ORCHESTRATOR = {"app.kubernetes.io/name": "choregos-orchestrator", "choregos/queue": "executor"}


def _push(ns: str, ip: str, run_id: str, public_key: str, *, labels: dict[str, str] | None = None) -> str:
    """POSTs an assignment SEALED to `public_key` from a pod of the namespace carrying `labels`
    (the orchestrator's by default); returns the HTTP status, `000` when the push never got in."""
    return _pushes(ns, ip, [run_id], public_key, labels=labels)[0]


def _pushes(
    ns: str, ip: str, run_ids: list[str], public_key: str, *, labels: dict[str, str] | None = None
) -> list[str]:
    """The pushes of `run_ids`, back to back from ONE pod: one HTTP status per run.

    Back to back, because the runner closes its port when its run ends; a second probe pod
    takes seconds to start, by which time a run with no reachable API has long ended and the
    second push meets a closed port (`000`), not the refusal it is there to prove. The API URL is
    a TEST-NET address (RFC 5737) for the same reason: nothing answers it, so the run waits on
    it instead of failing at once on a name that does not resolve."""
    secret = {"api_url": "http://192.0.2.1", "run_token": "t", "env": {}}
    curls = [
        f"curl -s -m 10 -o /dev/null -w '%{{http_code}}\\n' -H 'Content-Type: application/json' "
        f"-d '{json.dumps({'run_id': run_id, 'sealed': seal(public_key, run_id, secret)})}' "
        f"http://{ip}:8765/assign"
        for run_id in run_ids
    ]
    name = "push-" + "-".join(run_ids)
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": ns, "labels": ORCHESTRATOR if labels is None else labels},
        "spec": {
            "restartPolicy": "Never",
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "push",
                    "image": "curlimages/curl:8.11.1",
                    "command": ["sh", "-c", "; ".join(curls)],
                    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                }
            ],
        },
    }  # fmt: skip
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(pod))
    _wait(
        lambda: (_get(ns, "pod", name) or {}).get("status", {}).get("phase") in {"Succeeded", "Failed"},
        f"the push of {', '.join(run_ids)}",
    )
    return kubectl_shared("-n", ns, "logs", name).split()


@pytest.mark.capability("agent_sandbox", "snapshot")
def test_a_claim_adopts_a_warm_sandbox_that_holds_nothing_and_serves_one_run(ns: str) -> None:
    image = _runner_image()
    kubectl_shared("create", "serviceaccount", "choregos-runner", "-n", ns)
    kubectl_shared("-n", ns, "apply", "-f", "-", input_text=_render(ns, image))
    _wait(lambda: any(_ready(s) for s in _sandboxes(ns)), "a warm sandbox", timeout_s=420)
    (warm,) = [s["metadata"]["name"] for s in _sandboxes(ns)]

    # The pod the controller made: no service account token, no secret reference.
    pod_text = kubectl_shared("-n", ns, "get", "pod", warm, "-o", "json")
    pod = json.loads(pod_text)
    assert pod["spec"].get("automountServiceAccountToken") is False
    for forbidden in ("secretKeyRef", "secretRef", "kube-api-access", "serviceaccount/token"):
        assert forbidden not in pod_text, f"the warm pod carries `{forbidden}`"

    claim = {
        "apiVersion": "extensions.agents.x-k8s.io/v1beta1",
        "kind": "SandboxClaim",
        "metadata": {"name": "run-probe", "namespace": ns},
        "spec": {"warmPoolRef": {"name": "choregos-runner"}},
    }
    kubectl_shared("apply", "-f", "-", input_text=json.dumps(claim))
    _wait(
        lambda: (
            ((_get(ns, "sandboxclaim", "run-probe") or {}).get("status") or {}).get("sandbox", {}).get("name")
        ),
        "the claim to be bound",
    )
    bound = (_get(ns, "sandboxclaim", "run-probe") or {})["status"]["sandbox"]["name"]
    assert bound == warm, "the claim adopted the warm sandbox instead of making another"

    ip = (_get(ns, "sandbox", bound) or {})["status"]["podIPs"][0]
    # The key the runner printed at boot, read through the API server as the executor reads it.
    public_key = key_from_log(kubectl_shared("-n", ns, "logs", bound))
    assert public_key, "the warm runner printed no key: nothing could be sealed to it"
    _, other_key = new_key()
    # Another pod of the namespace cannot even reach the port: `choregos-runner-assignment`.
    assert _push(ns, ip, "run-z", public_key, labels={"app": "intruder"}) == "000", "the push got in"
    assert _push(ns, ip, "run-x", other_key) == "400", "sealed to another key: refused"
    first, second = _pushes(ns, ip, ["run-a", "run-b"], public_key)
    assert first == "200"
    assert second == "409", "a sandbox serves one run, never two"

    kubectl_shared("-n", ns, "delete", "sandboxclaim", "run-probe")
    _wait(lambda: _get(ns, "sandbox", bound) is None, "the claimed sandbox to be deleted")
    _wait(
        lambda: any(_ready(s) and s["metadata"]["name"] != bound for s in _sandboxes(ns)),
        "the pool to make a NEW warm sandbox",
        timeout_s=420,
    )
