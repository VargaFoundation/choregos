"""Tests « cluster » : ce qui n'est vrai que sur un vrai Kubernetes.

Une NetworkPolicy n'est pas une garantie tant qu'un CNI ne l'applique pas — Docker Desktop
et kindnet, par exemple, l'acceptent puis laissent tout passer. Ces tests s'exécutent contre
un cluster dont le CNI applique les politiques (kind + Calico) et sont **ignorés** sans lui.

    make cluster-up          # kind + Calico, ports locaux
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


def context() -> str:
    """Le contexte kube à utiliser, ou un `skip` qui dit précisément ce qui manque."""
    name = os.environ.get("CHOREGOS_CLUSTER_CONTEXT", "")
    if not name:
        pytest.skip("test cluster ignoré — CHOREGOS_CLUSTER_CONTEXT n'est pas défini")
    if shutil.which("kubectl") is None:
        pytest.skip("test cluster ignoré — kubectl absent")
    probe = subprocess.run(
        ["kubectl", "--context", name, "get", "ns", "-o", "name"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip(f"test cluster ignoré — contexte `{name}` injoignable : {probe.stderr[:120]}")
    return name


def kubectl(*args: str, input_text: str | None = None, check: bool = True) -> str:
    """`kubectl` sur le contexte du test. Rend la sortie ; lève si `check` et échec."""
    command = ["kubectl", "--context", context(), *args]
    result = subprocess.run(
        command, capture_output=True, text=True, input=input_text, timeout=180, check=False
    )
    if check and result.returncode != 0:
        raise AssertionError(f"{' '.join(command)} → {result.returncode}\n{result.stderr[:800]}")
    return result.stdout


def apply(manifests: Sequence[str]) -> None:
    for manifest in manifests:
        kubectl("apply", "-f", "-", input_text=manifest)


def run_probe(namespace: str, name: str, command: str, *, timeout_s: int = 180) -> str:
    """Lance un pod jetable qui exécute `command`, attend sa fin, rend ses logs.

    L'image est un curl minimal : la sonde doit pouvoir sortir *si* le réseau le permet,
    donc elle est tirée avant la politique par le nœud, pas par la sonde elle-même.
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
    assert phase in {"Succeeded", "Failed"}, f"la sonde n'a pas terminé (phase={phase or 'inconnue'})"
    return logs.strip()
