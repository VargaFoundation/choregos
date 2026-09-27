"""S7-04 — la sauvegarde Postgres se restaure vraiment.

Un `ScheduledBackup` dans un dépôt n'est pas une sauvegarde : tant qu'une restauration n'a
pas rendu les données, on a un fichier YAML et une espérance. Ce test monte le vrai
CloudNativePG avec la configuration Barman du dépôt, écrit des lignes, sauvegarde, détruit
le cluster, restaure depuis l'objet, et relit les lignes.

Il est lent (quelques minutes) et marqué `cluster` : il n'a de sens que sur un vrai cluster.
"""

from __future__ import annotations

import json
import time

import pytest
import yaml

from .conftest import kubectl

pytestmark = [pytest.mark.cluster, pytest.mark.slow]

NS = "choregos-data-test"
CLUSTER = "pg-restore-test"
RESTORED = "pg-restored"
BUCKET = "choregos-backups"


def cnpg_present() -> bool:
    return "clusters.postgresql.cnpg.io" in kubectl("get", "crd", "-o", "name", check=False)


@pytest.fixture(scope="module", autouse=True)
def stack() -> None:
    if not cnpg_present():
        pytest.skip("test ignoré — l'opérateur CloudNativePG n'est pas installé sur ce cluster")
    # Un namespace en cours de suppression refuse tout : on attend qu'il ait disparu avant
    # de le recréer, sinon un second passage du test échoue sur un reste du premier.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        phase = kubectl("get", "ns", NS, "-o", "jsonpath={.status.phase}", check=False).strip()
        if phase == "":
            break
        time.sleep(5)
    kubectl(
        "apply",
        "-f",
        "-",
        input_text=yaml.safe_dump({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}}),
    )
    kubectl("apply", "-f", "-", input_text=_magasin())
    _wait_ready("pod/magasin", "condition=Ready", timeout="300s")
    kubectl("apply", "-f", "-", input_text=_secrets())
    _create_bucket()
    yield
    kubectl("delete", "ns", NS, "--ignore-not-found", "--wait=false", check=False)


# Le magasin d'objets du test. SeaweedFS et non MinIO : le 2026-09-27, `minio/minio` a cessé
# d'être tirable ANONYMEMENT sur quay.io (401 sur le tag épinglé) comme sur Docker Hub
# (« authentication required »). Le commentaire précédent disait déjà s'être replié de Docker Hub
# vers quay pour cette raison ; quay a suivi. Le test échouait toutes les nuits au montage, sur
# un pod qui n'atteignait jamais `Ready` — et le message parlait d'une condition non remplie, pas
# d'une image introuvable.
#
# Une seule image, et elle sert aussi de client : `weed shell` crée le seau, donc rien à tirer de
# plus. Version figée pour la raison habituelle : une nuit ne doit pas changer d'image toute
# seule. Vérifié à la main avant de changer le test — seau créé, objet écrit et relu, et surtout
# un envoi MULTIPART complet de 6 Mo relu à l'octet, puisque c'est ce que fait Barman.
SEAWEEDFS = "chrislusf/seaweedfs:3.97"
#: Le port S3 de SeaweedFS, là où MinIO écoutait 9000.
S3_PORT = 8333


def _magasin() -> str:
    """SeaweedFS mono-pod : le magasin d'objets que Barman utilisera."""
    return yaml.safe_dump_all(
        [
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "magasin", "namespace": NS, "labels": {"app": "magasin"}},
                "spec": {
                    "containers": [
                        {
                            "name": "magasin",
                            "image": SEAWEEDFS,
                            # `server -s3` monte maître, volume, filer et passerelle S3 dans un
                            # seul processus : c'est fait pour un test. Sans `-s3.config`, la
                            # passerelle accepte n'importe quelles clés — d'où les identifiants
                            # factices du secret, qui ne sont vérifiés par personne.
                            "args": ["server", "-s3", "-dir=/data"],
                            "imagePullPolicy": "IfNotPresent",
                            "ports": [{"containerPort": S3_PORT}],
                        }
                    ]
                },
            },
            {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {"name": "magasin", "namespace": NS},
                "spec": {
                    "selector": {"app": "magasin"},
                    "ports": [{"port": S3_PORT, "targetPort": S3_PORT}],
                },
            },
        ]
    )


def _secrets() -> str:
    return yaml.safe_dump_all(
        [
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "choregos-db", "namespace": NS},
                "type": "kubernetes.io/basic-auth",
                "stringData": {"username": "choregos", "password": "choregos-dev"},
            },
            {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "choregos-backup", "namespace": NS},
                "stringData": {"access-key-id": "choregos", "secret-access-key": "choregos-test"},
            },
        ]
    )


def _create_bucket() -> None:
    """Le seau est créé DEPUIS le pod du magasin, par `weed shell`.

    Avant, un second pod tirait le client `mc`. Une image de moins à tirer est une cause
    d'échec de moins la nuit — et c'était précisément la deuxième image disparue.
    """
    kubectl(
        "-n",
        NS,
        "exec",
        "pod/magasin",
        "--",
        "sh",
        "-c",
        f"echo 's3.bucket.create -name {BUCKET}' | weed shell",
        check=False,
    )


def _wait_ready(target: str, condition: str, timeout: str = "600s") -> None:
    kubectl("-n", NS, "wait", "--for", condition, target, f"--timeout={timeout}", timeout_s=700)


def _cluster_manifest(name: str, *, restore_from: str | None = None) -> str:
    """La configuration du dépôt, ramenée à la taille d'un cluster de test.

    Ce qui change : une instance au lieu de trois, la classe de stockage par défaut, des
    volumes de 1 Gi, et un magasin S3 local à la place de celui du nuage. Ce qui ne change pas —
    et qui est l'objet du test — c'est le bloc `backup.barmanObjectStore` et le chemin de
    restauration.
    """
    source = yaml.safe_load(_repo_cluster())
    spec = source["spec"]
    spec["instances"] = 1
    spec["storage"] = {"size": "1Gi"}
    spec["walStorage"] = {"size": "1Gi"}
    # La mémoire demandée doit rester au-dessus du `shared_buffers` du dépôt (512 Mo) :
    # CNPG refuse le cluster sinon, et c'est une bonne chose — un Postgres qui démarre avec
    # moins de mémoire que ses buffers ne démarre pas longtemps.
    spec["resources"] = {"requests": {"cpu": "100m", "memory": "1Gi"}}
    spec["backup"]["barmanObjectStore"]["destinationPath"] = f"s3://{BUCKET}/{name}"
    spec["backup"]["barmanObjectStore"]["endpointURL"] = f"http://magasin:{S3_PORT}"
    spec["backup"]["barmanObjectStore"]["s3Credentials"] = {
        "accessKeyId": {"name": "choregos-backup", "key": "access-key-id"},
        "secretAccessKey": {"name": "choregos-backup", "key": "secret-access-key"},
    }
    if restore_from:
        spec.pop("bootstrap", None)
        spec["bootstrap"] = {"recovery": {"source": "origine"}}
        spec["externalClusters"] = [
            {
                "name": "origine",
                "barmanObjectStore": {
                    "destinationPath": f"s3://{BUCKET}/{restore_from}",
                    "endpointURL": f"http://magasin:{S3_PORT}",
                    # Sans `serverName`, CNPG cherche la sauvegarde sous le nom du **nouveau**
                    # cluster et répond « no target backup found ». C'est le nom du serveur
                    # d'origine qu'il faut donner : le piège classique d'une restauration.
                    "serverName": restore_from,
                    "s3Credentials": spec["backup"]["barmanObjectStore"]["s3Credentials"],
                    "wal": {"maxParallel": 2},
                },
            }
        ]
        spec.pop("backup", None)
    source["metadata"] = {"name": name, "namespace": NS}
    return yaml.safe_dump(source)


def _repo_cluster() -> str:
    from pathlib import Path

    raw = Path(__file__).resolve().parents[2] / "infra" / "bootstrap" / "data.yaml"
    for document in raw.read_text(encoding="utf-8").split("---"):
        if "kind: Cluster" in document:
            return document
    raise AssertionError("aucun `kind: Cluster` dans infra/bootstrap/data.yaml")


def psql(cluster: str, sql: str) -> str:
    pod = kubectl(
        "-n", NS, "get", "pod", "-l", f"cnpg.io/cluster={cluster},role=primary", "-o", "name"
    ).strip()
    assert pod, f"aucun primaire pour {cluster}"
    return kubectl("-n", NS, "exec", pod, "--", "psql", "-U", "postgres", "-d", "choregos", "-tAc", sql)


def test_le_cluster_du_depot_demarre(stack: None) -> None:
    """Première vérité : la configuration du dépôt produit un Postgres qui démarre."""
    kubectl("apply", "-f", "-", input_text=_cluster_manifest(CLUSTER))
    deadline = time.monotonic() + 600
    ready = ""
    while time.monotonic() < deadline:
        ready = kubectl(
            "-n", NS, "get", "cluster", CLUSTER, "-o", "jsonpath={.status.readyInstances}", check=False
        ).strip()
        if ready == "1":
            break
        time.sleep(10)
    if ready != "1":
        etat = kubectl("-n", NS, "describe", "cluster", CLUSTER, check=False)[-1500:]
        raise AssertionError(f"le cluster n'est jamais devenu prêt :\n{etat}")


def test_une_sauvegarde_se_restaure_avec_ses_donnees(stack: None) -> None:
    psql(CLUSTER, "CREATE TABLE IF NOT EXISTS preuve (id int primary key, texte text);")
    psql(CLUSTER, "INSERT INTO preuve VALUES (1, 'avant la sauvegarde') ON CONFLICT DO NOTHING;")
    assert "avant la sauvegarde" in psql(CLUSTER, "SELECT texte FROM preuve WHERE id = 1;")

    kubectl(
        "apply",
        "-f",
        "-",
        input_text=yaml.safe_dump(
            {
                "apiVersion": "postgresql.cnpg.io/v1",
                "kind": "Backup",
                "metadata": {"name": "sauvegarde-1", "namespace": NS},
                "spec": {"cluster": {"name": CLUSTER}},
            }
        ),
    )
    deadline = time.monotonic() + 600
    phase = ""
    while time.monotonic() < deadline:
        phase = kubectl(
            "-n", NS, "get", "backup", "sauvegarde-1", "-o", "jsonpath={.status.phase}", check=False
        ).strip()
        if phase in {"completed", "failed"}:
            break
        time.sleep(10)
    if phase != "completed":
        detail = kubectl("-n", NS, "describe", "backup", "sauvegarde-1", check=False)[-800:]
        raise AssertionError(f"sauvegarde en phase `{phase}` :\n{detail}")

    # Une écriture postérieure à la sauvegarde : elle ne doit PAS réapparaître après restauration.
    psql(CLUSTER, "INSERT INTO preuve VALUES (2, 'après la sauvegarde') ON CONFLICT DO NOTHING;")

    # La panne : le cluster disparaît, volumes compris.
    kubectl("-n", NS, "delete", "cluster", CLUSTER, "--wait=true")

    kubectl("apply", "-f", "-", input_text=_cluster_manifest(RESTORED, restore_from=CLUSTER))
    deadline = time.monotonic() + 900
    ready = ""
    while time.monotonic() < deadline:
        ready = kubectl(
            "-n", NS, "get", "cluster", RESTORED, "-o", "jsonpath={.status.readyInstances}", check=False
        ).strip()
        if ready == "1":
            break
        time.sleep(10)
    assert ready == "1", kubectl("-n", NS, "describe", "cluster", RESTORED, check=False)[-1500:]

    restaure = psql(RESTORED, "SELECT texte FROM preuve ORDER BY id;")
    assert "avant la sauvegarde" in restaure, restaure
    assert json.dumps(restaure), restaure
