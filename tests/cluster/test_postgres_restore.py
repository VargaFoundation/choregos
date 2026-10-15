"""S7-04 — the Postgres backup really restores.

A `ScheduledBackup` in a repository is not a backup: until a restore has returned the data,
there is a YAML file and a hope. This test stands up the real CloudNativePG with the
repository's Barman configuration, writes rows, backs up, destroys the cluster, restores from
the object store, and reads the rows back.

It is slow (a few minutes) and marked `cluster`: it only makes sense on a real cluster.
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
        pytest.skip("test skipped — the CloudNativePG operator is not installed on this cluster")
    # A namespace being deleted refuses everything: we wait until it has gone before recreating
    # it, otherwise a second run of the test fails on a leftover of the first.
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


# The test's object store. SeaweedFS and not MinIO: on 2026-09-27, `minio/minio` stopped being
# pullable ANONYMOUSLY on quay.io (401 on the pinned tag) as on Docker Hub ("authentication
# required"). The previous comment already said it had fallen back from Docker Hub to quay for
# this reason; quay followed. The test failed every night at setup, on a pod that never reached
# `Ready` — and the message spoke of an unmet condition, not of a missing image.
#
# A single image, and it also serves as the client: `weed shell` creates the bucket, so nothing
# more to pull. Version pinned for the usual reason: a night must not change image by itself.
# Checked by hand before changing the test — bucket created, object written and read back, and
# above all a full MULTIPART upload of 6 MB read back byte for byte, since that is what Barman does.
SEAWEEDFS = "chrislusf/seaweedfs:3.97"
#: SeaweedFS's S3 port, where MinIO listened on 9000.
S3_PORT = 8333


def _magasin() -> str:
    """Single-pod SeaweedFS: the object store Barman will use."""
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
                            # `server -s3` runs master, volume, filer and S3 gateway in a single
                            # process: it is made for a test. Without `-s3.config`, the gateway
                            # accepts any keys — hence the secret's dummy credentials, which
                            # nobody checks.
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
    """The bucket is created FROM the store pod, by `weed shell`.

    Before, a second pod pulled the `mc` client. One image fewer to pull is one cause of failure
    fewer at night — and it was precisely the second image that disappeared.
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
    """The repository's configuration, scaled down to a test cluster.

    What changes: one instance instead of three, the default storage class, 1 Gi volumes, and a
    local S3 store instead of the cloud one. What does not change — and is the object of the
    test — is the `backup.barmanObjectStore` block and the restore path.
    """
    source = yaml.safe_load(_repo_cluster())
    spec = source["spec"]
    spec["instances"] = 1
    spec["storage"] = {"size": "1Gi"}
    spec["walStorage"] = {"size": "1Gi"}
    # The requested memory must stay above the repository's `shared_buffers` (512 MB): CNPG
    # refuses the cluster otherwise, and that is a good thing — a Postgres that starts with less
    # memory than its buffers does not start for long.
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
                    # Without `serverName`, CNPG looks for the backup under the **new** cluster's
                    # name and answers "no target backup found". The original server's name
                    # must be given: the classic restore trap.
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
    raise AssertionError("no `kind: Cluster` in infra/bootstrap/data.yaml")


def psql(cluster: str, sql: str) -> str:
    pod = kubectl(
        "-n", NS, "get", "pod", "-l", f"cnpg.io/cluster={cluster},role=primary", "-o", "name"
    ).strip()
    assert pod, f"no primary for {cluster}"
    return kubectl("-n", NS, "exec", pod, "--", "psql", "-U", "postgres", "-d", "choregos", "-tAc", sql)


def test_le_cluster_du_depot_demarre(stack: None) -> None:
    """First truth: the repository's configuration produces a Postgres that starts."""
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
        raise AssertionError(f"the cluster never became ready:\n{etat}")


def test_une_sauvegarde_se_restaure_avec_ses_donnees(stack: None) -> None:
    psql(CLUSTER, "CREATE TABLE IF NOT EXISTS preuve (id int primary key, texte text);")
    psql(CLUSTER, "INSERT INTO preuve VALUES (1, 'before the backup') ON CONFLICT DO NOTHING;")
    assert "before the backup" in psql(CLUSTER, "SELECT texte FROM preuve WHERE id = 1;")

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
        raise AssertionError(f"backup in phase `{phase}`:\n{detail}")

    # A write made after the backup: it must NOT reappear after the restore.
    psql(CLUSTER, "INSERT INTO preuve VALUES (2, 'after the backup') ON CONFLICT DO NOTHING;")

    # The failure: the cluster disappears, volumes included.
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
    assert "before the backup" in restaure, restaure
    assert json.dumps(restaure), restaure
