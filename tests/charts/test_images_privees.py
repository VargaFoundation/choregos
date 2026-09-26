"""Le chart sait-il tirer une image d'un registre privé ?

Le chart n'a longtemps déclaré aucun `imagePullSecrets` — `rg` en rendait zéro. Tant que toutes
les images sont publiques, personne ne le voit. Dès qu'une ne l'est plus — une édition
entreprise (ADR 0024), un miroir Harbor fermé, une image maison — le déploiement échoue en
`ImagePullBackOff`, et la seule issue est de patcher le compte de service à la main, hors GitOps.

La propriété qui compte est la seconde : **tout pod livré par le chart** porte le secret, pas
seulement ceux qu'on a pensé à brancher le jour où on l'a écrit. Un Deployment ajouté demain
sans l'aide fera rougir ce test.
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

#: Les objets qui portent un podSpec.
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
    """Le défaut reste une plateforme d'images publiques."""
    for nom, spec in _pods(_rendu()):
        assert "imagePullSecrets" not in spec, f"{nom} porte un secret que personne n'a demandé"


@pytest.mark.parametrize("profil", (None, "local"))
def test_chaque_pod_du_chart_porte_le_secret_demande(profil: str | None) -> None:
    """Y compris les dépendances embarquées : une plateforme qui n'admet qu'un registre — le
    cas de Diametral, Kyverno `only-harbor-images` en Enforce — y fait passer TOUTES les images,
    pas seulement celles de Choregos."""
    surcharges = [f"global.imagePullSecrets={{{SECRET}}}"]
    docs = (
        _rendu(*surcharges)
        if profil is None
        else _rendu(*surcharges, _fichier=CHART / "values" / f"{profil}.yaml")
    )
    pods = _pods(docs)
    assert pods, "aucun pod rendu : le test ne vérifie plus rien"
    sans = [
        nom
        for nom, spec in pods
        if SECRET not in [entree.get("name") for entree in spec.get("imagePullSecrets", [])]
    ]
    assert not sans, (
        f"pods sans secret de tirage : {sans}. Sur un registre privé ils échoueront en "
        "ImagePullBackOff, et la seule issue sera de patcher hors GitOps."
    )


def test_plusieurs_secrets_passent_tous() -> None:
    """Un locataire peut avoir un registre pour la plateforme et un pour son édition."""
    docs = _rendu("global.imagePullSecrets={harbor-prive,ghcr-entreprise}")
    for nom, spec in _pods(docs):
        noms = [entree.get("name") for entree in spec.get("imagePullSecrets", [])]
        assert noms == ["harbor-prive", "ghcr-entreprise"], f"{nom} : {noms}"
