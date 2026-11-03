"""Does the chart know how to pull an image from a private registry?

For a long time the chart declared no `imagePullSecrets` -- `rg` rendered zero. As long as all
images are public, nobody sees it. As soon as one is not -- an enterprise
edition (ADR 0024), a closed Harbor mirror, a home-made image -- the deployment fails with
`ImagePullBackOff`, and the only way out is to patch the service account by hand, outside GitOps.

The property that matters is the second: **every pod shipped by the chart** carries the secret, not
only those someone remembered to wire up the day it was written. A Deployment added tomorrow
without the helper will turn this test red.
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
SECRET = "harbor-prive"

#: The objects that carry a podSpec.
PORTEURS = {"Deployment", "StatefulSet", "Job", "DaemonSet"}

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendu(*surcharges: str, _fichier: pathlib.Path | None = None) -> list[dict[str, Any]]:
    commande = ["helm", "template", "choregos", str(CHART)]
    if _fichier is not None:
        commande += ["-f", str(_fichier)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    sortie = subprocess.run(commande, capture_output=True, text=True, check=True, timeout=120)
    return [doc for doc in yaml.safe_load_all(sortie.stdout) if doc]


def _pods(docs: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    trouves = []
    for doc in docs:
        if doc.get("kind") in PORTEURS:
            spec = doc["spec"]["template"]["spec"]
            trouves.append((f"{doc['kind']}/{doc['metadata']['name']}", spec))
    return trouves


def test_sans_valeur_aucun_secret_n_est_rendu() -> None:
    """The default remains a platform of public images."""
    for nom, spec in _pods(_rendu()):
        assert "imagePullSecrets" not in spec, f"{nom} carries a secret nobody asked for"


@pytest.mark.parametrize("profil", (None, "local"))
def test_chaque_pod_du_chart_porte_le_secret_demande(profil: str | None) -> None:
    """Including the embedded dependencies: a platform that admits only one registry -- the
    Diametral case, Kyverno `only-harbor-images` in Enforce -- routes ALL images through it,
    not only Choregos's."""
    surcharges = [f"global.imagePullSecrets={{{SECRET}}}"]
    docs = (
        _rendu(*surcharges)
        if profil is None
        else _rendu(*surcharges, _fichier=CHART / "values" / f"{profil}.yaml")
    )
    pods = _pods(docs)
    assert pods, "no pod rendered: the test no longer checks anything"
    sans = [
        nom
        for nom, spec in pods
        if SECRET not in [entree.get("name") for entree in spec.get("imagePullSecrets", [])]
    ]
    assert not sans, (
        f"pods without a pull secret: {sans}. On a private registry they will fail with "
        "ImagePullBackOff, and the only way out will be to patch outside GitOps."
    )


def test_plusieurs_secrets_passent_tous() -> None:
    """A tenant can have one registry for the platform and one for its edition."""
    docs = _rendu("global.imagePullSecrets={harbor-prive,ghcr-entreprise}")
    for nom, spec in _pods(docs):
        noms = [entree.get("name") for entree in spec.get("imagePullSecrets", [])]
        assert noms == ["harbor-prive", "ghcr-entreprise"], f"{nom} : {noms}"


def _runner_account(docs: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        doc
        for doc in docs
        if doc.get("kind") == "ServiceAccount" and doc["metadata"]["name"] == "choregos-runner"
    )


def _worker_envs(docs: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    envs: dict[str, dict[str, str]] = {}
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc["metadata"]["name"].startswith("choregos-orchestrator"):
            container = doc["spec"]["template"]["spec"]["containers"][0]
            envs[doc["metadata"]["name"]] = {
                entry["name"]: entry["value"] for entry in container.get("env", []) if "value" in entry
            }
    return envs


@pytest.mark.parametrize("executor", ("agent_sandbox", "k8s_job"))
def test_the_runner_account_carries_the_pull_secrets(executor: str) -> None:
    """S51-09: a job step naming its own image runs as a `k8s_job` pod of `choregos-runner`; the
    account carried no pull secret, and an image from a private registry project never pulled."""
    docs = _rendu(
        f"global.runner.executor={executor}", "global.imagePullSecrets={harbor-prive,ghcr-entreprise}"
    )
    names = [entry["name"] for entry in _runner_account(docs).get("imagePullSecrets", [])]
    assert names == ["harbor-prive", "ghcr-entreprise"]
    assert "imagePullSecrets" not in _runner_account(_rendu(f"global.runner.executor={executor}"))


def test_every_worker_hands_the_pull_secrets_to_the_runner_pods_it_creates() -> None:
    """The pod a `k8s_job` executor creates is not rendered by the chart: the worker names the
    secrets in its spec, from `CHOREGOS_RUNNER_IMAGE_PULL_SECRETS`."""
    envs = _worker_envs(_rendu("global.imagePullSecrets={harbor-prive,ghcr-entreprise}"))
    assert envs, "no orchestrator worker rendered: the test no longer checks anything"
    for name, env in envs.items():
        assert env.get("CHOREGOS_RUNNER_IMAGE_PULL_SECRETS") == "harbor-prive,ghcr-entreprise", name
    for name, env in _worker_envs(_rendu()).items():
        assert env.get("CHOREGOS_RUNNER_IMAGE_PULL_SECRETS") == "", name
