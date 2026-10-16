"""Do the deployment and the image speak of the same edition?

Choregos exists in two editions ([ADR 0024](../../docs/adr/0024-deux-editions.md)) and the edition
that runs is **not** a chart value: it is the enterprise plugin that declares it when
loading. Nothing linked the two. An operator who writes `global.edition: enterprise` on
community images got a platform that starts, looks healthy, and refuses the second
organization -- the symptom arriving weeks after the cause, on an apparently unrelated action.

Two guards, at the two places where the gap can arise: the chart render refuses an enterprise
edition without a pull secret (the registry is private, that is all of phase 1's access
control), and the API refuses to start if what is announced is not what is loaded.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHART = RACINE / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _rendre(*surcharges: str) -> subprocess.CompletedProcess[str]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    return subprocess.run(commande, capture_output=True, text=True, check=False)


def test_une_edition_entreprise_sans_secret_de_tirage_est_refusee() -> None:
    """Without the secret, the pods would stay in `ImagePullBackOff` without ever naming the cause."""
    rendu = _rendre("global.edition=enterprise")
    assert rendu.returncode != 0, "the chart rendered an enterprise edition without a pull secret"
    assert "global.imagePullSecrets" in rendu.stderr, (
        f"the refusal does not name the missing value: {rendu.stderr[-400:]}"
    )


def test_une_edition_inconnue_est_refusee() -> None:
    """`pro`, `pricey`, `Enterprise`: a typo must not yield a silent deployment."""
    rendu = _rendre("global.edition=pro")
    assert rendu.returncode != 0
    assert "community" in rendu.stderr and "enterprise" in rendu.stderr


def test_l_edition_entreprise_avec_son_secret_se_rend() -> None:
    """The guard must not forbid the enterprise edition -- only announcing it halfway."""
    rendu = _rendre("global.edition=enterprise", "global.imagePullSecrets[0].name=harbor-prive")
    assert rendu.returncode == 0, rendu.stderr[-400:]
    assert 'value: "enterprise"' in rendu.stdout


def test_chaque_processus_recoit_l_edition_annoncee() -> None:
    """The API and the workers must receive it: that is where the startup refusal happens.

    A Deployment added tomorrow without the `commonEnv` helper would not know which edition is announced,
    and would start as community without saying so.
    """
    rendu = _rendre()
    assert rendu.returncode == 0, rendu.stderr[-400:]
    sans: list[str] = []
    for doc in yaml.safe_load_all(rendu.stdout):
        if not doc or doc.get("kind") not in {"Deployment", "StatefulSet", "DaemonSet"}:
            continue
        nom = doc["metadata"]["name"]
        if "choregos-web" in nom:  # the front end reads no platform setting
            continue
        conteneurs = doc["spec"]["template"]["spec"].get("containers", [])
        noms = {v["name"] for c in conteneurs for v in (c.get("env") or [])}
        if "CHOREGOS_EDITION" not in noms:
            sans.append(nom)
    assert not sans, f"these processes do not know which edition is announced: {sans}"
