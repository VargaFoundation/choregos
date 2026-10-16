"""Can the migration hook start? Checked on the rendered chart.

A `pre-install` hook (which Argo CD translates to PreSync) runs BEFORE any ordinary resource
of the release. The migrations Job ran under `choregos-api`, which is an ordinary
resource: the controller answered "error looking up service account
choregos/choregos-api: serviceaccount not found", no pod was born, and the sync
waited for a hook that could not start. The dev tenant paid for it on 2026-09-26 --
seven hours, zero pods, the whole Sync blocked behind a Job without a pod.

The kind bench never saw this defect: `values/local.yaml` switches the hook to `post-install`
because the database is embedded there. The chart's DEFAULT stayed broken. This test
holds it in both modes, by rendering the chart for each environment.

Without `helm`, it skips and says so.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHART = RACINE / "charts" / "choregos"
ENVIRONNEMENTS = ("local", "dev", "staging", "prod")

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendu(env: str) -> list[dict[str, Any]]:
    resultat = subprocess.run(
        ["helm", "template", "choregos", str(CHART), "-f", str(CHART / "values" / f"{env}.yaml")],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return [d for d in yaml.safe_load_all(resultat.stdout) if d]


def _un(documents: list[dict[str, Any]], kind: str, nom: str) -> dict[str, Any]:
    trouves = [d for d in documents if d.get("kind") == kind and d.get("metadata", {}).get("name") == nom]
    assert len(trouves) == 1, f"{kind}/{nom} expected once, found {len(trouves)} times"
    return trouves[0]


def _hook(doc: dict[str, Any]) -> dict[str, str]:
    annotations = (doc.get("metadata", {}).get("annotations") or {}).items()
    return {k: v for k, v in annotations if k.startswith("helm.sh/")}


@pytest.mark.parametrize("env", ENVIRONNEMENTS)
def test_le_job_de_migration_ne_depend_d_aucune_ressource_ordinaire(env: str) -> None:
    """Its service account is created by the SAME hook, so it exists when the Job starts."""
    documents = _rendu(env)
    jobs = [d for d in documents if d.get("kind") == "Job" and "migrations" in d["metadata"]["name"]]
    if not jobs:
        pytest.skip(f"{env} does not render a migration Job")
    job = jobs[0]
    compte = job["spec"]["template"]["spec"].get("serviceAccountName")
    assert compte == "choregos-migrations", (
        f'{env}: the Job runs under "{compte}". If it is not a hook account, the pod '
        "is never created in pre-install/PreSync."
    )
    sa = _un(documents, "ServiceAccount", compte)
    assert _hook(sa)["helm.sh/hook"] == _hook(job)["helm.sh/hook"], (
        f"{env}: the account and the Job are not in the same hook -- one will go without the other"
    )
    assert int(_hook(sa)["helm.sh/hook-weight"]) < int(_hook(job)["helm.sh/hook-weight"]), (
        f"{env}: the account must weigh less than the Job to be created before it"
    )


@pytest.mark.parametrize("env", ENVIRONNEMENTS)
def test_le_compte_du_hook_ne_monte_pas_de_jeton(env: str) -> None:
    """The migrations only talk to the database: no Kubernetes API call, so
    no reason to carry a service account token."""
    documents = _rendu(env)
    comptes = [
        d
        for d in documents
        if d.get("kind") == "ServiceAccount" and d["metadata"]["name"] == "choregos-migrations"
    ]
    if not comptes:
        pytest.skip(f"{env} does not render the hook account")
    assert comptes[0].get("automountServiceAccountToken") is False


def test_les_annotations_de_compte_de_service_suivent_sur_le_compte_du_hook() -> None:
    """A database that authenticates through workload identity (IRSA, Workload
    Identity) carries that identity in the annotations: the hook must have them too,
    otherwise the migrations are the ONLY component that cannot connect."""
    resultat = subprocess.run(
        [
            "helm",
            "template",
            "choregos",
            str(CHART),
            "-f",
            str(CHART / "values" / "prod.yaml"),
            "--set",
            "global.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn=arn:aws:iam::1:role/choregos",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    documents = [d for d in yaml.safe_load_all(resultat.stdout) if d]
    sa = _un(documents, "ServiceAccount", "choregos-migrations")
    annotations = sa["metadata"]["annotations"]
    assert annotations["eks.amazonaws.com/role-arn"] == "arn:aws:iam::1:role/choregos"
    # And the caller's annotations did not overwrite the hook's.
    assert annotations["helm.sh/hook-weight"] == "-10"
