"""Loss of a **node** while a worker runs on it (S12-05).

`test_platform_on_cluster.py` kills a *pod*: Kubernetes replaces it within seconds, and the run
resumes. This file removes the **node** under the pod, which is a different failure: the API
server learns nothing right away, it waits until it no longer receives a heartbeat, then waits
again for the pod's toleration before evicting. The time between the two is time during which
the work does not move, and nobody sees it.

Fidelity: stopping a kind node's container is exactly "the node disappeared" from the API
server's point of view — the kubelet stops answering, with no clean termination. A spot
eviction is *gentler* (30 seconds' notice and a taint). What passes here therefore passes there;
what is not covered is the handling of the notice, which the platform does not implement.

The node is restarted at the end: the test leaves the cluster as it found it.
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest
import yaml

from .conftest import kubectl

pytestmark = [pytest.mark.cluster, pytest.mark.slow]

NS = "choregos-node-loss-test"
IMAGE = "choregos-api:dev"
# The worker is pinned here, the API elsewhere: losing the worker's node must not take the API down.
WORKER_NODE = "choregos-worker2"
API_NODE = "choregos-worker"


def docker(*args: str, check: bool = True) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)} : {result.stderr.strip()}")
    return result.stdout


def node_names() -> set[str]:
    payload = json.loads(kubectl("get", "nodes", "-o", "json"))
    return {item["metadata"]["name"] for item in payload["items"]}


@pytest.fixture(scope="module", autouse=True)
def stack() -> None:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False).returncode:
        pytest.skip(f"image `{IMAGE}` is absent — `docker build -f docker/api.Dockerfile --target api`")
    missing = {WORKER_NODE, API_NODE} - node_names()
    if missing:
        pytest.skip(f"nodes missing from the cluster: {sorted(missing)} — this test wants `make cluster-up`")

    kubectl("delete", "ns", NS, "--ignore-not-found", "--wait=true", timeout_s=180, check=False)
    kubectl(
        "apply",
        "-f",
        "-",
        input_text=yaml.safe_dump({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}}),
    )
    kubectl("apply", "-f", "-", input_text=_stack())
    kubectl("-n", NS, "rollout", "status", "deploy/api", "--timeout=240s", timeout_s=300)
    kubectl("-n", NS, "rollout", "status", "deploy/worker", "--timeout=240s", timeout_s=300)
    yield
    # The node first: a namespace does not delete cleanly if a node is missing.
    docker("start", WORKER_NODE, check=False)
    _wait_node_ready(WORKER_NODE, timeout_s=180)
    kubectl("delete", "ns", NS, "--ignore-not-found", "--wait=false", check=False)


def _stack() -> str:
    """Two deployments with no external dependency: what is measured is the replacement, not the app.

    `CHOREGOS_FAKES=1` and no database: the test is about scheduling, and one more dependency
    would only add reasons to fail that have nothing to do with losing a node.
    """

    def deployment(name: str, node: str, tolerations: list[dict[str, object]] | None) -> dict[str, object]:
        pod_spec: dict[str, object] = {
            "nodeSelector": {"kubernetes.io/hostname": node},
            "containers": [
                {
                    "name": name,
                    "image": IMAGE,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sleep", "3600"],
                    "resources": {"requests": {"cpu": "10m", "memory": "32Mi"}},
                }
            ],
        }
        if tolerations is not None:
            pod_spec["tolerations"] = tolerations
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": name, "namespace": NS},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": name}},
                "template": {"metadata": {"labels": {"app": name}}, "spec": pod_spec},
            },
        }

    # The worker is NOT pinned by a nodeSelector for its replacement: it is left free to restart
    # elsewhere. The pinning only serves to choose where it starts.
    worker = deployment("worker", WORKER_NODE, None)
    del worker["spec"]["template"]["spec"]["nodeSelector"]  # type: ignore[index]
    worker["spec"]["template"]["spec"]["affinity"] = {  # type: ignore[index]
        "nodeAffinity": {
            "preferredDuringSchedulingIgnoredDuringExecution": [
                {
                    "weight": 100,
                    "preference": {
                        "matchExpressions": [
                            {"key": "kubernetes.io/hostname", "operator": "In", "values": [WORKER_NODE]}
                        ]
                    },
                }
            ]
        }
    }
    return yaml.safe_dump_all([deployment("api", API_NODE, None), worker])


def _wait_node_ready(node: str, timeout_s: float = 180.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        conditions = kubectl(
            "get",
            "node",
            node,
            "-o",
            r"jsonpath={.status.conditions[?(@.type=='Ready')].status}",
            check=False,
        ).strip()
        if conditions == "True":
            return
        time.sleep(3)
    raise AssertionError(f"node {node} did not become Ready again within {timeout_s:.0f}s")


def pod_on(node: str, app: str) -> str | None:
    payload = json.loads(kubectl("-n", NS, "get", "pods", "-l", f"app={app}", "-o", "json"))
    for item in payload["items"]:
        if item["spec"].get("nodeName") == node and item["metadata"].get("deletionTimestamp") is None:
            return str(item["metadata"]["name"])
    return None


def pod_uids(app: str) -> set[str]:
    payload = json.loads(kubectl("-n", NS, "get", "pods", "-l", f"app={app}", "-o", "json"))
    return {str(item["metadata"]["uid"]) for item in payload["items"]}


def new_running_pod_elsewhere(app: str, gone: str, known: set[str]) -> str | None:
    """A pod **created after** the cut, ready, on another node.

    Looking for "a pod elsewhere" is not enough: an earlier rollout may have left one, and the
    test then returns two seconds believing it measured a replacement. The comparison is
    therefore on the UIDs known before the failure — a pod that is not among them is truly a
    replacement.
    """
    payload = json.loads(kubectl("-n", NS, "get", "pods", "-l", f"app={app}", "-o", "json"))
    for item in payload["items"]:
        if str(item["metadata"]["uid"]) in known:
            continue
        node = item["spec"].get("nodeName")
        phase = item.get("status", {}).get("phase")
        ready = all(
            c.get("status") == "True"
            for c in item.get("status", {}).get("conditions", [])
            if c.get("type") == "Ready"
        )
        if node and node != gone and phase == "Running" and ready:
            return str(item["metadata"]["name"])
    return None


def single_pod_on(app: str, node: str, timeout_s: float = 180.0) -> str:
    """Wait until **only one** pod remains, on the wanted node — otherwise the measurement is skewed."""
    deadline = time.monotonic() + timeout_s
    last: list[tuple[str, str]] = []
    while time.monotonic() < deadline:
        payload = json.loads(kubectl("-n", NS, "get", "pods", "-l", f"app={app}", "-o", "json"))
        alive = [
            (str(i["metadata"]["name"]), str(i["spec"].get("nodeName", "")))
            for i in payload["items"]
            if i["metadata"].get("deletionTimestamp") is None
            and i.get("status", {}).get("phase") == "Running"
        ]
        last = alive
        if len(alive) == 1 and alive[0][1] == node:
            return alive[0][0]
        time.sleep(3)
    raise AssertionError(f"not exactly one `{app}` pod on {node}: {last}")


def test_un_noeud_perdu_est_remarque_puis_le_travail_repart(stack: None) -> None:
    """How long the work stays still — the number nobody measures.

    Three instants matter: the node's disappearance, the moment Kubernetes declares it
    unreachable, and the moment the pod runs elsewhere. The gap between the last two is the pod's
    toleration, whose **default is five minutes**.
    """
    original = single_pod_on("worker", WORKER_NODE)
    known = pod_uids("worker")
    api_before = pod_on(API_NODE, "api")
    assert api_before is not None

    t0 = time.monotonic()
    docker("stop", WORKER_NODE)

    # 1. Kubernetes eventually stops receiving a heartbeat.
    not_ready_at = None
    deadline = t0 + 180
    while time.monotonic() < deadline:
        status = kubectl(
            "get",
            "node",
            WORKER_NODE,
            "-o",
            r"jsonpath={.status.conditions[?(@.type=='Ready')].status}",
            check=False,
        ).strip()
        if status and status != "True":
            not_ready_at = time.monotonic()
            break
        time.sleep(2)
    assert not_ready_at is not None, "the stopped node was never marked NotReady"

    # 2. …then the pod restarts elsewhere. That wait is the one that costs.
    replacement = None
    deadline = t0 + 480
    while time.monotonic() < deadline:
        replacement = new_running_pod_elsewhere("worker", WORKER_NODE, known)
        if replacement:
            break
        time.sleep(5)
    rescheduled_at = time.monotonic()

    detection = not_ready_at - t0
    total = rescheduled_at - t0
    print(f"\nnode lost → NotReady: {detection:.0f}s; node lost → worker restarted: {total:.0f}s")

    assert replacement is not None, (
        f"the worker did not restart within {total:.0f}s. With the default tolerations "
        "(`node.kubernetes.io/unreachable`, 300 s) that is expected — and that is the problem: "
        "see `docs/runbooks/perte-de-noeud.md`."
    )
    assert replacement != original

    # 3. The API saw nothing: it is on another node.
    assert pod_on(API_NODE, "api") == api_before, "the API moved although its node was intact"


def test_une_tolerance_courte_divise_l_attente(stack: None) -> None:
    """The same failure, with `tolerationSeconds: 20`: the replacement arrives in tens of seconds.

    This is the fix, and it can be measured. The node was stopped by the previous test: this test
    puts it back into service itself — the fixture is module-scoped and only cleans up at the end.
    """
    docker("start", WORKER_NODE, check=False)
    _wait_node_ready(WORKER_NODE)
    tolerations = [
        {"key": key, "operator": "Exists", "effect": "NoExecute", "tolerationSeconds": 20}
        for key in ("node.kubernetes.io/unreachable", "node.kubernetes.io/not-ready")
    ]
    patch = {
        "spec": {
            "template": {
                "spec": {
                    "tolerations": tolerations,
                    "nodeSelector": {"kubernetes.io/hostname": WORKER_NODE},
                }
            }
        }
    }
    kubectl("-n", NS, "patch", "deploy/worker", "--type=merge", "-p", json.dumps(patch))
    kubectl("-n", NS, "rollout", "status", "deploy/worker", "--timeout=180s", timeout_s=240)

    # The nodeSelector served to put it on the right node; it is removed so the pod can leave.
    kubectl(
        "-n",
        NS,
        "patch",
        "deploy/worker",
        "--type=json",
        "-p",
        json.dumps([{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]),
    )
    # Removing the nodeSelector triggers a rollout: wait until only one pod remains, on the
    # targeted node. Without that, a pod from the previous rollout lingers elsewhere and the test
    # "measures" two seconds — a green test that measures nothing.
    single_pod_on("worker", WORKER_NODE)
    known = pod_uids("worker")

    t0 = time.monotonic()
    docker("stop", WORKER_NODE)
    replacement = None
    deadline = t0 + 240
    while time.monotonic() < deadline:
        replacement = new_running_pod_elsewhere("worker", WORKER_NODE, known)
        if replacement:
            break
        time.sleep(3)
    total = time.monotonic() - t0
    print(f"\nwith tolerationSeconds=20: worker restarted in {total:.0f}s")

    assert replacement is not None, f"no replacement within {total:.0f}s despite the short toleration"
    # The reference measurement is 354 s; a 20 s toleration must divide the wait, not nibble at
    # it. The bound is wide to absorb the image download time on a node that never carried this
    # pod.
    assert total < 150, f"the short toleration did not reduce the wait ({total:.0f}s)"
    assert total > 10, (
        f"replacement in {total:.0f}s: too fast to be a reaction to the failure — "
        "a pod was already running elsewhere and the measurement measures nothing"
    )
