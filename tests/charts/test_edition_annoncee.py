"""Le déploiement et l'image parlent-ils de la même édition ?

Choregos existe en deux éditions ([ADR 0024](../../docs/adr/0024-deux-editions.md)) et l'édition
qui tourne n'est **pas** une valeur du chart : c'est le greffon entreprise qui la déclare en se
chargeant. Rien ne reliait les deux. Un exploitant qui écrit `global.edition: enterprise` sur des
images communautaires obtenait une plateforme qui démarre, paraît saine, et refuse la seconde
organisation — le symptôme arrivant des semaines après la cause, sur une action sans rapport
apparent.

Deux gardes, aux deux endroits où l'écart peut naître : le rendu du chart refuse une édition
entreprise sans secret de tirage (le registre est privé, c'est tout le contrôle d'accès de la
phase 1), et l'API refuse de démarrer si ce qui est annoncé n'est pas ce qui est chargé.
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
    """Sans le secret, les pods resteraient en `ImagePullBackOff` sans jamais nommer la cause."""
    rendu = _rendre("global.edition=enterprise")
    assert rendu.returncode != 0, "le chart a rendu une édition entreprise sans secret de tirage"
    assert "global.imagePullSecrets" in rendu.stderr, (
        f"le refus ne nomme pas la valeur qui manque : {rendu.stderr[-400:]}"
    )


def test_une_edition_inconnue_est_refusee() -> None:
    """`pro`, `pricey`, `Enterprise` : une faute de frappe ne doit pas donner un déploiement muet."""
    rendu = _rendre("global.edition=pro")
    assert rendu.returncode != 0
    assert "community" in rendu.stderr and "enterprise" in rendu.stderr


def test_l_edition_entreprise_avec_son_secret_se_rend() -> None:
    """La garde ne doit pas interdire l'édition entreprise — seulement l'annoncer à moitié."""
    rendu = _rendre("global.edition=enterprise", "global.imagePullSecrets[0].name=harbor-prive")
    assert rendu.returncode == 0, rendu.stderr[-400:]
    assert 'value: "enterprise"' in rendu.stdout


def test_chaque_processus_recoit_l_edition_annoncee() -> None:
    """L'API et les workers doivent la recevoir : c'est là que le refus au démarrage a lieu.

    Un Deployment ajouté demain sans l'aide `commonEnv` ne saurait pas quelle édition on annonce,
    et démarrerait en communautaire sans le dire.
    """
    rendu = _rendre()
    assert rendu.returncode == 0, rendu.stderr[-400:]
    sans: list[str] = []
    for doc in yaml.safe_load_all(rendu.stdout):
        if not doc or doc.get("kind") not in {"Deployment", "StatefulSet", "DaemonSet"}:
            continue
        nom = doc["metadata"]["name"]
        if "choregos-web" in nom:  # le front ne lit aucun réglage de plateforme
            continue
        conteneurs = doc["spec"]["template"]["spec"].get("containers", [])
        noms = {v["name"] for c in conteneurs for v in (c.get("env") or [])}
        if "CHOREGOS_EDITION" not in noms:
            sans.append(nom)
    assert not sans, f"ces processus ne savent pas quelle édition on annonce : {sans}"
