"""\"Cluster\" tests: what is only true on a real Kubernetes.

A NetworkPolicy is not a guarantee until a CNI enforces it — Docker Desktop and kindnet, for
example, accept it and then let everything through. These tests run against a cluster whose CNI
enforces policies (kind + Calico) and are **skipped** without one.

    make cluster-up          # kind + Calico, local ports
    CHOREGOS_CLUSTER_CONTEXT=kind-choregos uv run pytest tests/cluster -m cluster
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Sequence

import pytest


def context(*, non_destructive: bool = False) -> str:
    """The kube context to use, or a `skip` that says exactly what is missing.

    `non_destructive`: the caller only creates objects in a namespace of its own and deletes
    it afterwards (the agent-sandbox tests, S35). Such a test may run on a shared cluster
    named, explicitly and exactly, by `CHOREGOS_CLUSTER_ALLOW_SHARED=<context>`; every other
    test keeps refusing it.
    """
    name = os.environ.get("CHOREGOS_CLUSTER_CONTEXT", "")
    if not name:
        pytest.skip("cluster test skipped — CHOREGOS_CLUSTER_CONTEXT is not set")
    shared_ok = non_destructive and os.environ.get("CHOREGOS_CLUSTER_ALLOW_SHARED", "") == name
    # These tests stop nodes, create and destroy namespaces, install operators and cut the
    # network. On a disposable cluster that is their job; on a shared cluster it is an incident.
    # A developer machine's contexts often include one of each — `dev` and `prod` next to
    # `kind-choregos` — and one badly copied variable is enough. A kind cluster is therefore
    # required, unless an exemption is written in black and white.
    if (
        not shared_ok
        and not name.startswith("kind-")
        and os.environ.get("CHOREGOS_CLUSTER_ALLOW_NON_KIND") != "je-sais-ce-que-je-fais"  # lang: fr-ok
    ):
        pytest.skip(
            f"cluster test refused on `{name}`: it is not a kind cluster. These tests are "
            "destructive (node stop, network cut, database restore). For a disposable cluster "
            "that is not kind: CHOREGOS_CLUSTER_ALLOW_NON_KIND=je-sais-ce-que-je-fais"  # lang: fr-ok
        )
    if shutil.which("kubectl") is None:
        pytest.skip("cluster test skipped — kubectl is absent")
    probe = subprocess.run(
        ["kubectl", "--context", name, "get", "ns", "-o", "name"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip(f"cluster test skipped — context `{name}` unreachable: {probe.stderr[:120]}")
    return name


def kubectl(*args: str, input_text: str | None = None, check: bool = True, timeout_s: float = 180) -> str:
    """`kubectl` on the test's context. Returns the output; raises if `check` and it fails.

    `timeout_s` comfortably exceeds the `--timeout` passed to `kubectl wait`: the first start
    pulls images, and a process timeout shorter than the requested wait turns slowness into a
    test failure.
    """
    return _kubectl(context(), *args, input_text=input_text, check=check, timeout_s=timeout_s)


def kubectl_shared(
    *args: str, input_text: str | None = None, check: bool = True, timeout_s: float = 180
) -> str:
    """`kubectl` for a NON-destructive test, allowed on the shared cluster that
    `CHOREGOS_CLUSTER_ALLOW_SHARED` names (see `context`)."""
    return _kubectl(
        context(non_destructive=True), *args, input_text=input_text, check=check, timeout_s=timeout_s
    )


def _kubectl(name: str, *args: str, input_text: str | None, check: bool, timeout_s: float) -> str:
    command = ["kubectl", "--context", name, *args]
    result = subprocess.run(
        command, capture_output=True, text=True, input=input_text, timeout=timeout_s, check=False
    )
    if check and result.returncode != 0:
        raise AssertionError(f"{' '.join(command)} → {result.returncode}\n{result.stderr[:800]}")
    return result.stdout


def apply(manifests: Sequence[str]) -> None:
    for manifest in manifests:
        kubectl("apply", "-f", "-", input_text=manifest)


def run_probe(namespace: str, name: str, command: str, *, timeout_s: int = 180) -> str:
    """Run a disposable pod that executes `command`, wait for it to end, return its logs.

    The image is a minimal curl: the probe must be able to get out *if* the network allows it,
    so it is pulled before the policy by the node, not by the probe itself.
    """
    kubectl("-n", namespace, "delete", "pod", name, "--ignore-not-found", check=False)
    manifest = json.dumps(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": name, "namespace": namespace},
            "spec": {
                "restartPolicy": "Never",
                "securityContext": {
                    "runAsNonRoot": True,
                    "runAsUser": 1000,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [
                    {
                        "name": "probe",
                        "image": "curlimages/curl:8.11.1",
                        "command": ["sh", "-c", command],
                        "securityContext": {
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                    }
                ],
            },
        }
    )
    kubectl("apply", "-f", "-", input_text=manifest)
    deadline = time.monotonic() + timeout_s
    phase = ""
    while time.monotonic() < deadline:
        phase = kubectl(
            "-n", namespace, "get", "pod", name, "-o", "jsonpath={.status.phase}", check=False
        ).strip()
        if phase in {"Succeeded", "Failed"}:
            break
        time.sleep(3)
    logs = kubectl("-n", namespace, "logs", name, check=False)
    kubectl("-n", namespace, "delete", "pod", name, "--ignore-not-found", check=False)
    assert phase in {"Succeeded", "Failed"}, f"the probe did not finish (phase={phase or 'unknown'})"
    return logs.strip()
