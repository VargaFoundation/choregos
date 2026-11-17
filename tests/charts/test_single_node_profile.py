"""S80-07b, ADR 0184: the single-node profile renders one machine's installation, with its backup.

The profile is `charts/choregos/values/single-node.yaml`. What it must hold: one replica of
everything, no autoscaler and no disruption budget (there is no other node to move to), its volumes on
`local-path`, the embedded dependencies accepted in `prod` only together with the scheduled backup,
and a backup CronJob that dumps every database, copies Temporal and the memory, and ships off the
machine. The bench values (development secrets and sign-in, demonstration fakes) stay refused.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
PROFILE = CHART / "values" / "single-node.yaml"
SITE = [
    "--set",
    "backup.target.s3.url=s3://backups/choregos",
    "--set",
    "backup.target.s3.endpoint=http://s3:8333",
]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART), "-n", "choregos", *arguments]
    return subprocess.run(command, capture_output=True, text=True, check=check)


def _documents(*arguments: str) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(_render("-f", str(PROFILE), *SITE, *arguments).stdout) if d]


def _one(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    found = [d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name]
    assert len(found) == 1, f"{kind}/{name}: {len(found)} rendered"
    return found[0]


def _pod(document: dict[str, Any]) -> dict[str, Any]:
    spec: dict[str, Any] = document["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return spec


def test_every_workload_has_one_replica_and_nothing_scales_it() -> None:
    documents = _documents()
    workloads = [d for d in documents if d["kind"] in ("Deployment", "StatefulSet")]
    assert {d["metadata"]["name"] for d in workloads} >= {
        "choregos-api",
        "choregos-web",
        "choregos-orchestrator-orchestrator",
        "choregos-orchestrator-executor",
        "choregos-orchestrator-tracker",
        "choregos-orchestrator-memory",
        "choregos-postgresql",
        "choregos-temporal",
        "choregos-litellm",
        "choregos-ecphoria",
    }
    assert {
        d["metadata"]["name"]: d["spec"]["replicas"] for d in workloads if d["spec"]["replicas"] != 1
    } == {}
    kinds = {d["kind"] for d in documents}
    assert "HorizontalPodAutoscaler" not in kinds
    assert "PodDisruptionBudget" not in kinds


def test_no_pod_asks_to_be_spread_over_nodes() -> None:
    for document in _documents():
        if document["kind"] not in ("Deployment", "StatefulSet"):
            continue
        affinity = document["spec"]["template"]["spec"].get("affinity") or {}
        assert "podAntiAffinity" not in affinity, document["metadata"]["name"]


def test_the_volumes_are_on_local_path_and_the_dependencies_are_embedded() -> None:
    documents = _documents()
    claims = {d["metadata"]["name"]: d["spec"] for d in documents if d["kind"] == "PersistentVolumeClaim"}
    assert claims["choregos-temporal"]["storageClassName"] == "local-path"
    assert claims["choregos-ecphoria"]["storageClassName"] == "local-path"
    postgres = _one(documents, "StatefulSet", "choregos-postgresql")
    assert postgres["spec"]["volumeClaimTemplates"][0]["spec"]["storageClassName"] == "local-path"


def test_the_requests_fit_the_stated_machine() -> None:
    """8 vCPU and 32 GiB (the profile's header): the platform's requests leave room for 4 agent runs."""
    cpu = memory = 0.0
    for document in _documents():
        if document["kind"] not in ("Deployment", "StatefulSet"):
            continue
        replicas = document["spec"]["replicas"]
        for container in document["spec"]["template"]["spec"]["containers"]:
            requests = (container.get("resources") or {}).get("requests") or {}
            assert requests, f"{document['metadata']['name']}/{container['name']} requests nothing"
            cpu += replicas * _cpu(requests["cpu"])
            memory += replicas * _gib(requests["memory"])
    agents_cpu, agents_memory = 4 * 2, 4 * 4  # runner.maxActive x runner.limits
    assert cpu <= 4, cpu
    assert memory + agents_memory <= 32 - 4, memory  # 4 GiB left to k3s and the system
    assert cpu + agents_cpu <= 8 + 4, cpu  # CPU limits may overcommit; requests never do


def _cpu(value: str | int | float) -> float:
    text = str(value)
    return float(text[:-1]) / 1000 if text.endswith("m") else float(text)


def _gib(value: str) -> float:
    units = {"Mi": 1 / 1024, "Gi": 1.0}
    return float(value[:-2]) * units[value[-2:]]


def test_the_backup_dumps_every_database_copies_the_files_and_ships_off_the_machine() -> None:
    documents = _documents()
    cronjob = _one(documents, "CronJob", "choregos-backup")
    assert cronjob["spec"]["schedule"] == "0 2 * * *"
    assert cronjob["spec"]["concurrencyPolicy"] == "Forbid"
    pod = _pod(cronjob)
    assert [c["name"] for c in pod["initContainers"]] == ["prepare", "database", "files"]
    assert [c["name"] for c in pod["containers"]] == ["ship"]
    claims = {v["name"]: v.get("persistentVolumeClaim", {}).get("claimName") for v in pod["volumes"]}
    assert claims["temporal"] == "choregos-temporal"
    assert claims["memory"] == "choregos-ecphoria"
    ship = {e["name"]: e.get("value") for e in pod["containers"][0]["env"]}
    assert ship["TARGET_KIND"] == "s3"
    assert ship["TARGET_PATH"] == "backups/choregos"
    assert ship["RCLONE_CONFIG_TARGET_ENDPOINT"] == "http://s3:8333"
    assert ship["KEEP"] == "7"
    scripts = _one(documents, "ConfigMap", "choregos-backup")["data"]
    assert "pg_dump -Fc --create" in scripts["database.sh"]
    assert "pg_database WHERE NOT datistemplate" in scripts["database.sh"]
    assert ".backup(" in scripts["files.py"]
    assert 'touch "$dir/COMPLETE"' in scripts["ship.sh"]


def test_the_restore_is_a_job_template_that_never_runs_by_itself() -> None:
    documents = _documents()
    restore = _one(documents, "CronJob", "choregos-restore")
    assert restore["spec"]["suspend"] is True
    pod = _pod(restore)
    assert [c["name"] for c in pod["initContainers"]] == ["fetch", "database", "files"]
    scripts = _one(documents, "ConfigMap", "choregos-backup")["data"]
    assert "pg_restore --create" in scripts["restore-database.sh"]
    assert "COMPLETE" in scripts["fetch.sh"]


def test_the_object_store_is_copied_when_asked() -> None:
    documents = _documents(
        "--set",
        "backup.objectStore=true",
        "--set",
        "global.objectStore.secretRef=choregos-objects",
        "--set",
        "global.objectStore.url=s3://files/choregos",
    )
    for name in ("choregos-backup", "choregos-restore"):
        objects = next(
            c for c in _pod(_one(documents, "CronJob", name))["initContainers"] if c["name"] == "objects"
        )
        env = {e["name"]: e.get("value") for e in objects["env"]}
        assert env["OBJECTS_PATH"] == "files/choregos"


def test_a_volume_target_gets_a_claim_that_outlives_the_release() -> None:
    documents = _documents("--set", "backup.target.kind=volume")
    claim = _one(documents, "PersistentVolumeClaim", "choregos-backup")
    assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    pod = _pod(_one(documents, "CronJob", "choregos-backup"))
    backup = next(v for v in pod["volumes"] if v["name"] == "backup")
    assert backup["persistentVolumeClaim"]["claimName"] == "choregos-backup"


def test_the_backup_pods_pass_the_chart_s_pod_security() -> None:
    for name in ("choregos-backup", "choregos-restore"):
        pod = _pod(_one(_documents(), "CronJob", name))
        assert pod["securityContext"]["runAsNonRoot"] is True
        for container in pod["initContainers"] + pod["containers"]:
            context = container["securityContext"]
            assert context["allowPrivilegeEscalation"] is False
            assert context["capabilities"]["drop"] == ["ALL"]
            assert container["resources"]["limits"]


def test_without_the_profile_there_is_no_backup() -> None:
    kinds = {(d["kind"], d["metadata"]["name"]) for d in yaml.safe_load_all(_render().stdout) if d}
    assert ("CronJob", "choregos-backup") not in kinds


def test_single_node_without_a_backup_is_refused() -> None:
    result = _render("-f", str(PROFILE), "--set", "backup.enabled=false", check=False)
    assert result.returncode != 0
    assert "singleNode.enabled=true needs backup.enabled=true" in result.stderr


def test_embedded_dependencies_in_prod_are_still_refused_outside_the_profile() -> None:
    result = _render("-f", str(PROFILE), *SITE, "--set", "singleNode.enabled=false", check=False)
    assert result.returncode != 0
    assert "global.database.embedded" in result.stderr


@pytest.mark.parametrize(
    "value", ["global.devSecrets=true", "global.devLogin.enabled=true", "demoFakes.enabled=true"]
)
def test_the_bench_values_stay_refused_in_the_profile(value: str) -> None:
    result = _render("-f", str(PROFILE), *SITE, "--set", value, check=False)
    assert result.returncode != 0
    assert value.split("=", maxsplit=1)[0] in result.stderr


def test_an_s3_target_without_a_bucket_is_refused() -> None:
    result = _render("-f", str(PROFILE), check=False)
    assert result.returncode != 0
    assert "backup.target.s3.url" in result.stderr
