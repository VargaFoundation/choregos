"""Le hook de migration peut-il démarrer ? Vérifié sur le rendu du chart.

Un hook `pre-install` (que Argo CD traduit en PreSync) s'exécute AVANT toute ressource
ordinaire de la version. Le Job des migrations tournait sous `choregos-api`, qui est une
ressource ordinaire : le contrôleur répondait « error looking up service account
choregos/choregos-api: serviceaccount not found », aucun pod ne naissait, et la synchro
attendait un hook qui ne pouvait pas démarrer. Le locataire dev l'a payé le 2026-09-26 —
sept heures, zéro pod, toute la Sync bloquée derrière un Job sans pod.

Le banc kind n'a jamais vu ce défaut : `values/local.yaml` passe le hook en `post-install`
parce que la base y est embarquée. Le DÉFAUT du chart, lui, est resté cassé. Ce test le
tient dans les deux modes, en rendant le chart pour chaque environnement.

Sans `helm`, il s'ignore et le dit.
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
    assert len(trouves) == 1, f"{kind}/{nom} attendu une fois, trouvé {len(trouves)} fois"
    return trouves[0]


def _hook(doc: dict[str, Any]) -> dict[str, str]:
    annotations = (doc.get("metadata", {}).get("annotations") or {}).items()
    return {k: v for k, v in annotations if k.startswith("helm.sh/")}


@pytest.mark.parametrize("env", ENVIRONNEMENTS)
def test_le_job_de_migration_ne_depend_d_aucune_ressource_ordinaire(env: str) -> None:
    """Son compte de service est créé par le MÊME hook, donc il existe quand le Job démarre."""
    documents = _rendu(env)
    jobs = [d for d in documents if d.get("kind") == "Job" and "migrations" in d["metadata"]["name"]]
    if not jobs:
        pytest.skip(f"{env} ne rend pas de Job de migration")
    job = jobs[0]
    compte = job["spec"]["template"]["spec"].get("serviceAccountName")
    assert compte == "choregos-migrations", (
        f"{env} : le Job tourne sous « {compte} ». Si ce n'est pas un compte du hook, le pod "
        "n'est jamais créé en pre-install/PreSync."
    )
    sa = _un(documents, "ServiceAccount", compte)
    assert _hook(sa)["helm.sh/hook"] == _hook(job)["helm.sh/hook"], (
        f"{env} : le compte et le Job ne sont pas dans le même hook — l'un partira sans l'autre"
    )
    assert int(_hook(sa)["helm.sh/hook-weight"]) < int(_hook(job)["helm.sh/hook-weight"]), (
        f"{env} : le compte doit peser moins que le Job pour être créé avant lui"
    )


@pytest.mark.parametrize("env", ENVIRONNEMENTS)
def test_le_compte_du_hook_ne_monte_pas_de_jeton(env: str) -> None:
    """Les migrations ne parlent qu'à la base : aucun appel à l'API Kubernetes, donc
    aucune raison de porter un jeton de compte de service."""
    documents = _rendu(env)
    comptes = [
        d
        for d in documents
        if d.get("kind") == "ServiceAccount" and d["metadata"]["name"] == "choregos-migrations"
    ]
    if not comptes:
        pytest.skip(f"{env} ne rend pas le compte du hook")
    assert comptes[0].get("automountServiceAccountToken") is False


def test_les_annotations_de_compte_de_service_suivent_sur_le_compte_du_hook() -> None:
    """Une base qui s'authentifie par identité de charge de travail (IRSA, Workload
    Identity) porte cette identité dans les annotations : le hook doit les avoir aussi,
    sinon les migrations sont le SEUL composant qui ne peut pas se connecter."""
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
    # Et les annotations de l'appelant n'ont pas écrasé celles du hook.
    assert annotations["helm.sh/hook-weight"] == "-10"
