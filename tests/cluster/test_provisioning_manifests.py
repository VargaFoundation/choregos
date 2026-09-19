"""S8-06 — les manifests d'un template sont acceptés par un vrai Kubernetes.

`helm template` et `kubeconform` disent qu'un YAML est bien formé. Ils ne disent pas qu'un
`ResourceQuota` est cohérent, qu'un `RuntimeClass` existe, qu'un rôle référence une API
connue, ni qu'un `PipelineRun` passe la validation d'admission. Seul le serveur d'API le dit,
et c'est ce que ce test lui demande — en `--dry-run=server`, donc sans rien créer.
"""

from __future__ import annotations

import subprocess

import pytest
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core import load_preset
from choregos_orchestrator.gitops import render_project_manifests

from .conftest import context, kubectl

pytestmark = pytest.mark.cluster

SLUG = "manifests-test"


def manifests(preset: str = "team") -> dict[str, str]:
    config = ProjectConfig(slug=SLUG, org="varga", repo=RepoConfig(url="https://github.com/varga/demo.git"))
    return render_project_manifests(SLUG, config, load_preset(preset))


def server_dry_run(manifest: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kubectl", "--context", context(), "apply", "--dry-run=server", "-f", "-"],
        input=manifest,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.fixture(scope="module", autouse=True)
def namespaces() -> None:
    """Les namespaces d'abord : le reste y vit, un dry-run serveur les exige existants."""
    kubectl("apply", "-f", "-", input_text=manifests()["namespaces.yaml"])
    yield
    kubectl("delete", "ns", f"proj-{SLUG}-runners", f"proj-{SLUG}-ci", "--ignore-not-found", check=False)


@pytest.mark.parametrize(
    "name",
    ["namespaces.yaml", "quotas.yaml", "netpol.yaml", "rbac.yaml"],
)
def test_le_serveur_accepte_les_manifests_du_projet(name: str) -> None:
    result = server_dry_run(manifests()[name])
    assert result.returncode == 0, f"{name} refusé :\n{result.stderr[:600]}"


def test_les_quotas_sont_ceux_de_la_politique() -> None:
    kubectl("apply", "-f", "-", input_text=manifests()["quotas.yaml"])
    quota = kubectl(
        "-n", f"proj-{SLUG}-runners", "get", "resourcequota", "-o", "jsonpath={.items[0].spec.hard}"
    )
    assert "cpu" in quota and "memory" in quota, quota


def test_le_runtimeclass_gvisor_n_est_exige_que_si_la_politique_le_demande() -> None:
    """`gvisor` n'existe pas sur tous les clusters : le template ne doit pas l'imposer."""
    avec = manifests("regulated")
    sans = manifests("solo")
    assert "gvisor" in avec.get("runtimeclass.yaml", "") or "gvisor" in avec.get("tekton.yaml", "")
    assert "runtimeClassName: gvisor" not in sans.get("tekton.yaml", "")


def test_un_manifest_tekton_sans_operateur_echoue_clairement() -> None:
    """Sans Tekton installé, le refus doit nommer la ressource inconnue, pas un YAML illisible."""
    result = server_dry_run(manifests()["tekton.yaml"])
    if result.returncode == 0:
        pytest.skip("Tekton est installé sur ce cluster : rien à prouver ici")
    sortie = (result.stderr + result.stdout).lower()
    assert "no matches for kind" in sortie or "not found" in sortie, result.stderr[:400]
