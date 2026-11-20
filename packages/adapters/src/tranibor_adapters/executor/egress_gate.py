# SPDX-License-Identifier: Apache-2.0
"""The egress gate in a pod spec (S80-07f, ADR 0184 "Amendment: the egress gate").

The gate (`tranibor_core.egress_gate`) is the FIRST init container of a pod that runs agent-controlled
code: before a hosted agent's sidecar (ADR 0080) and before the agent's own container (ADR 0146),
which start before the runner. Init containers run in order and a native sidecar starts only once the
init containers before it have completed, so nothing of the agent's runs until the gate has seen the
pod's egress policy in force. A gate that fails fails the pod: `restartPolicy: Never`.

Its image is the runner's: no image to ship or pull. It writes what it measured to a small volume the
runner container mounts read-only, and the runner journals it (`egress.gate`).
"""

from __future__ import annotations

from typing import Any

from tranibor_contracts.env import with_legacy_entries
from tranibor_core.egress_gate import GATE_CONTAINER, REPORT_DIR, REPORT_ENV, REPORT_FILE, EgressGate
from tranibor_core.exit_codes import explanation

#: The termination reason an executor reports when the gate ended the pod.
GATE_REASON = "EgressGateFailed"
#: The volume the gate writes its report to.
GATE_VOLUME = "egress-gate"
#: The interpreter of the runner's image (its venv is not on `PATH`, #713), the API's and the
#: worker's too: the gate runs from any of them.
PYTHON = "/app/.venv/bin/python"


def init_container(image: str, gate: EgressGate) -> dict[str, Any]:
    """The gate's container. Least privilege, as the strict Kyverno rule wants every init container."""
    return {
        "name": GATE_CONTAINER,
        "image": image,
        "command": [PYTHON, "-m", "tranibor_core.egress_gate", *gate.args()],
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "readOnlyRootFilesystem": True,
            "runAsNonRoot": True,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumeMounts": [{"name": GATE_VOLUME, "mountPath": REPORT_DIR}],
    }


def volume() -> dict[str, Any]:
    return {"name": GATE_VOLUME, "emptyDir": {"sizeLimit": "1Mi"}}


def runner_mount() -> dict[str, Any]:
    return {"name": GATE_VOLUME, "mountPath": REPORT_DIR, "readOnly": True}


def runner_env() -> dict[str, str]:
    return {"name": REPORT_ENV, "value": f"{REPORT_DIR}/{REPORT_FILE}"}


def add_to_pod(pod_spec: dict[str, Any], image: str, gate: EgressGate, runner: str = "runner") -> None:
    """Puts the gate FIRST among the pod's init containers, and its report where the runner reads it."""
    pod_spec["initContainers"] = [init_container(image, gate), *pod_spec.get("initContainers", [])]
    pod_spec.setdefault("volumes", []).append(volume())
    container = next(c for c in pod_spec["containers"] if c["name"] == runner)
    container.setdefault("volumeMounts", []).append(runner_mount())
    container.setdefault("env", []).extend(with_legacy_entries([runner_env()]))


def gate_failure(pod: Any) -> tuple[int, str] | None:
    """The gate's exit code and what it says, when it ended the pod; `None` otherwise.

    Read before the runner's own status: a pod whose gate failed has a runner that never started
    (`PodInitializing`), and that alone would not say why."""
    statuses = ((pod or {}).get("status") or {}).get("initContainerStatuses") or []
    gate = next((s for s in statuses if s.get("name") == GATE_CONTAINER), None)
    if gate is None:
        return None
    ended = (gate.get("state") or {}).get("terminated") or (gate.get("lastState") or {}).get("terminated")
    if not ended or not ended.get("exitCode"):
        return None
    code = int(ended["exitCode"])
    meaning = explanation(code)
    return code, f"egress gate exited with {code}{': ' + meaning if meaning else ''} (phase: isolation)"
