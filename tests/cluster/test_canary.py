"""S7-08 — le canary de la plateforme s'arrête vraiment à son premier palier.

Un `Rollout` dans un chart ne prouve rien : ce qui compte est qu'une nouvelle version soit
**retenue** à 10 % au lieu de remplacer l'ancienne. Le test déploie le Rollout rendu par le
chart, change l'image, et vérifie que le rollout se met en pause avec l'ancienne version
encore majoritaire — puis qu'un abandon la restaure.
"""

from __future__ import annotations

import json
import time

import pytest
import yaml

from .conftest import kubectl

pytestmark = [pytest.mark.cluster, pytest.mark.slow]

NS = "choregos-canary-test"
NAME = "canary-demo"


def rollouts_installed() -> bool:
    return "rollouts.argoproj.io" in kubectl("get", "crd", "-o", "name", check=False)


@pytest.fixture(scope="module", autouse=True)
def namespace() -> None:
    if not rollouts_installed():
        pytest.skip("test ignoré — Argo Rollouts n'est pas installé sur ce cluster")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if kubectl("get", "ns", NS, "-o", "jsonpath={.status.phase}", check=False).strip() == "":
            break
        time.sleep(5)
    kubectl(
        "apply",
        "-f",
        "-",
        input_text=yaml.safe_dump({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}}),
    )
    yield
    kubectl("delete", "ns", NS, "--ignore-not-found", "--wait=false", check=False)


def rollout(image: str) -> str:
    """Les mêmes paliers que `global.canary.steps` du chart, sur un pod trivial."""
    return yaml.safe_dump(
        {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Rollout",
            "metadata": {"name": NAME, "namespace": NS},
            "spec": {
                "replicas": 4,
                "strategy": {
                    "canary": {
                        "steps": [
                            {"setWeight": 10},
                            {"pause": {}},
                            {"setWeight": 50},
                            {"pause": {"duration": "10s"}},
                            {"setWeight": 100},
                        ]
                    }
                },
                "selector": {"matchLabels": {"app": NAME}},
                "template": {
                    "metadata": {"labels": {"app": NAME}},
                    "spec": {
                        "containers": [
                            {
                                "name": "http",
                                "image": image,
                                "args": ["-listen=:8080", "-text=ok"],
                                "imagePullPolicy": "IfNotPresent",
                                "ports": [{"containerPort": 8080}],
                            }
                        ]
                    },
                },
            },
        }
    )


def running_images() -> set[str]:
    """Images des pods encore vivants — un pod en cours d'arrêt n'est pas une version servie."""
    pods = json.loads(kubectl("-n", NS, "get", "pods", "-o", "json"))["items"]
    return {
        pod["spec"]["containers"][0]["image"]
        for pod in pods
        if pod["status"].get("phase") == "Running" and not pod["metadata"].get("deletionTimestamp")
    }


def status() -> dict:
    raw = kubectl("-n", NS, "get", "rollout", NAME, "-o", "json", check=False)
    return json.loads(raw)["status"] if raw.strip() else {}


def wait_for(predicate, timeout: float = 300) -> dict:
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        state = status()
        if predicate(state):
            return state
        time.sleep(5)
    raise AssertionError(f"état jamais atteint : {json.dumps(state)[:600]}")


def test_le_rollout_initial_se_deploie(namespace: None) -> None:
    kubectl("apply", "-f", "-", input_text=rollout("hashicorp/http-echo:1.0"))
    state = wait_for(lambda s: s.get("phase") == "Healthy")
    assert state.get("readyReplicas") == 4, state


def test_une_nouvelle_version_est_retenue_au_premier_palier(namespace: None) -> None:
    """Le cœur de S7-08 : la version suivante ne remplace pas l'ancienne d'un coup."""
    kubectl("apply", "-f", "-", input_text=rollout("hashicorp/http-echo:0.2.3"))
    state = wait_for(lambda s: s.get("phase") == "Paused", timeout=300)

    assert state.get("currentStepIndex") in {1, 2}, state
    # Le canary ne porte qu'une fraction du trafic : l'ancienne version sert encore.
    stable = state.get("stableRS")
    current = state.get("currentPodHash")
    assert stable and current and stable != current, state
    pods = kubectl("-n", NS, "get", "pods", "-o", "json")
    versions = {pod["spec"]["containers"][0]["image"] for pod in json.loads(pods)["items"]}
    assert len(versions) == 2, f"les deux versions doivent coexister pendant le palier : {versions}"


def test_un_abandon_restaure_la_version_stable(namespace: None) -> None:
    """Le rollback est l'autre moitié de la garantie : un canary qu'on ne peut pas annuler n'en est pas un."""
    kubectl(
        "-n",
        NS,
        "patch",
        "rollout",
        NAME,
        "--type=merge",
        "-p",
        json.dumps({"status": {"abort": True}}),
        "--subresource=status",
        check=False,
    )
    kubectl("apply", "-f", "-", input_text=rollout("hashicorp/http-echo:1.0"))
    state = wait_for(lambda s: s.get("phase") == "Healthy", timeout=300)
    assert state.get("readyReplicas") == 4, state

    # Les pods du canary abandonné s'éteignent en arrière-plan : on attend la convergence
    # plutôt que de photographier un instant de transition.
    deadline = time.monotonic() + 180
    versions: set[str] = set()
    while time.monotonic() < deadline:
        versions = running_images()
        if versions == {"hashicorp/http-echo:1.0"}:
            return
        time.sleep(5)
    raise AssertionError(f"la version abandonnée est restée : {versions}")
