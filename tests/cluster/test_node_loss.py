"""Perte d'un **nœud** pendant qu'un worker tourne dessus (S12-05).

`test_platform_on_cluster.py` tue un *pod* : Kubernetes le remplace en quelques secondes, et le
run reprend. Ce fichier retire le **nœud** sous le pod, ce qui est une autre panne : l'API server
n'apprend rien tout de suite, il attend de ne plus recevoir de battement de cœur, puis attend
encore la tolérance de la pod avant d'évincer. Le temps entre les deux est du temps pendant lequel
le travail ne bouge pas, et personne ne le voit.

Fidélité : arrêter le conteneur d'un nœud kind, c'est exactement « le nœud a disparu » du point de
vue de l'API server — le kubelet cesse de répondre, sans terminaison propre. Une éviction spot est
*plus douce* (30 secondes de préavis et un taint). Ce qui passe ici passe donc là-bas ; ce qui n'est
pas couvert est la gestion du préavis, que la plateforme n'implémente pas.

Le nœud est redémarré à la fin : le test rend le cluster comme il l'a trouvé.
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
# Le worker est épinglé ici, l'API ailleurs : la perte du nœud du worker ne doit pas emporter l'API.
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
        pytest.skip(f"image `{IMAGE}` absente — `docker build -f docker/api.Dockerfile`")
    missing = {WORKER_NODE, API_NODE} - node_names()
    if missing:
        pytest.skip(f"nœuds absents du cluster : {sorted(missing)} — ce test veut `make cluster-up`")

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
    # Le nœud d'abord : un namespace ne se supprime pas proprement si un nœud manque.
    docker("start", WORKER_NODE, check=False)
    _wait_node_ready(WORKER_NODE, timeout_s=180)
    kubectl("delete", "ns", NS, "--ignore-not-found", "--wait=false", check=False)


def _stack() -> str:
    """Deux déploiements sans dépendance externe : ce qui est mesuré est le replacement, pas l'appli.

    `CHOREGOS_FAKES=1` et pas de base : le test porte sur l'ordonnancement, et une dépendance de
    plus ne ferait qu'ajouter des raisons d'échouer qui n'ont rien à voir avec la perte d'un nœud.
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

    # Le worker n'est PAS épinglé par un nodeSelector pour son remplaçant : on le laisse libre de
    # repartir ailleurs. L'épinglage sert seulement à choisir où il démarre.
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
    raise AssertionError(f"le nœud {node} n'est pas redevenu Ready en {timeout_s:.0f}s")


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
    """Un pod **créé après** la coupure, prêt, sur un autre nœud.

    Chercher « un pod ailleurs » ne suffit pas : un rollout antérieur peut en avoir laissé un, et
    le test rend alors deux secondes en croyant mesurer un replacement. La comparaison porte donc
    sur les UID connus avant la panne — un pod qui n'y figure pas est bien un remplaçant.
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
    """Attend qu'il ne reste **qu'un** pod, sur le nœud voulu — sinon la mesure est faussée."""
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
    raise AssertionError(f"pas exactement un pod `{app}` sur {node} : {last}")


def test_un_noeud_perdu_est_remarque_puis_le_travail_repart(stack: None) -> None:
    """Combien de temps le travail reste immobile — c'est le nombre que personne ne mesure.

    Trois instants comptent : la disparition du nœud, le moment où Kubernetes le déclare
    injoignable, et le moment où le pod tourne ailleurs. L'écart entre les deux derniers est la
    tolérance de la pod, dont **le défaut est de cinq minutes**.
    """
    original = single_pod_on("worker", WORKER_NODE)
    known = pod_uids("worker")
    api_before = pod_on(API_NODE, "api")
    assert api_before is not None

    t0 = time.monotonic()
    docker("stop", WORKER_NODE)

    # 1. Kubernetes finit par ne plus recevoir de battement de cœur.
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
    assert not_ready_at is not None, "le nœud arrêté n'a jamais été marqué NotReady"

    # 2. …puis le pod repart ailleurs. C'est cette attente-là qui coûte.
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
    print(f"\nnœud perdu → NotReady : {detection:.0f}s ; nœud perdu → worker reparti : {total:.0f}s")

    assert replacement is not None, (
        f"le worker n'est pas reparti en {total:.0f}s. Avec les tolérances par défaut "
        "(`node.kubernetes.io/unreachable`, 300 s), c'est attendu — et c'est le problème : "
        "voir `docs/runbooks/perte-de-noeud.md`."
    )
    assert replacement != original

    # 3. L'API, elle, n'a rien vu : elle est sur un autre nœud.
    assert pod_on(API_NODE, "api") == api_before, "l'API a bougé alors que son nœud était intact"


def test_une_tolerance_courte_divise_l_attente(stack: None) -> None:
    """La même panne, avec `tolerationSeconds: 20` : le remplacement arrive en dizaines de secondes.

    C'est le correctif, et il se mesure. Le nœud a été arrêté par le test précédent : ce test le
    remet en service lui-même — la fixture est à portée de module et ne nettoie qu'à la fin.
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

    # Le nodeSelector a servi à le poser sur le bon nœud ; on le retire pour qu'il puisse partir.
    kubectl(
        "-n",
        NS,
        "patch",
        "deploy/worker",
        "--type=json",
        "-p",
        json.dumps([{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]),
    )
    # Retirer le nodeSelector relance un rollout : attendre qu'il ne reste qu'un pod, sur le nœud
    # visé. Sans cela, un pod du rollout précédent traîne ailleurs et le test « mesure » deux
    # secondes — un test vert qui ne mesure rien.
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
    print(f"\navec tolerationSeconds=20 : worker reparti en {total:.0f}s")

    assert replacement is not None, f"pas de remplacement en {total:.0f}s malgré la tolérance courte"
    # La mesure de référence est de 354 s ; une tolérance de 20 s doit diviser l'attente, pas la
    # grignoter. La borne est large pour absorber le temps de téléchargement d'image sur un nœud
    # qui n'a jamais porté ce pod.
    assert total < 150, f"la tolérance courte n'a pas réduit l'attente ({total:.0f}s)"
    assert total > 10, (
        f"remplacement en {total:.0f}s : trop rapide pour être une réaction à la panne — "
        "un pod tournait déjà ailleurs et la mesure ne mesure rien"
    )
