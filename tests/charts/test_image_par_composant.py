"""Une image par composant (S17-03) : l'édition entreprise sert son API et son orchestrateur depuis
un projet privé ; la console, la base, Temporal et le reste viennent du cœur.

Le chart ne savait composer qu'un registre : `global.imageRegistry` pour toutes les images. Servir
les images `-ee` voulait donc tout servir depuis le projet privé — ou forker le chart.
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
PRIVE = "harbor.build.diametral.com/choregos-ee"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _images(*surcharges: str) -> dict[str, list[str]]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    sortie = subprocess.run(commande, capture_output=True, text=True, check=True, timeout=120)
    images: dict[str, list[str]] = {}
    for doc in yaml.safe_load_all(sortie.stdout):
        if not doc or doc.get("kind") not in {"Deployment", "StatefulSet", "Job"}:
            continue
        spec: dict[str, Any] = doc["spec"]["template"]["spec"]
        conteneurs = spec.get("initContainers", []) + spec.get("containers", [])
        images[f"{doc['kind']}/{doc['metadata']['name']}"] = [c["image"] for c in conteneurs]
    return images


EE = (
    f"choregos-api.image.registry={PRIVE}",
    "choregos-api.image.repository=choregos-api-ee",
    f"choregos-orchestrator.image.registry={PRIVE}",
    "choregos-orchestrator.image.repository=choregos-orchestrator-ee",
)


def test_l_api_et_l_orchestrateur_viennent_du_projet_prive_le_reste_du_coeur() -> None:
    images = _images(*EE)
    prives = {nom for nom, refs in images.items() if any(r.startswith(PRIVE) for r in refs)}
    assert any("api" in nom for nom in prives) and any("orchestrator" in nom for nom in prives), prives
    # Le job de migration lance l'image de l'API : il suit son registre.
    assert any(nom.startswith("Job/") for nom in prives), prives
    for nom, refs in images.items():
        if nom not in prives:
            assert all(not r.startswith(PRIVE) for r in refs), (nom, refs)
    assert any(r.startswith("ghcr.io/vargafoundation/choregos-web") for refs in images.values() for r in refs)


def test_le_digest_global_ne_designe_pas_une_image_d_un_autre_registre() -> None:
    images = _images(*EE, "global.imageDigest=sha256:" + "a" * 64)
    for nom, refs in images.items():
        for ref in refs:
            if ref.startswith(PRIVE):
                assert "@sha256" not in ref, f"{nom} : le digest du cœur posé sur une image de l'EE"
            elif ref.startswith("ghcr.io/vargafoundation/choregos-"):
                assert "@sha256:" in ref, f"{nom} : le cœur garde son digest"


def test_sans_surcharge_rien_ne_change() -> None:
    for nom, refs in _images().items():
        assert all(not r.startswith(PRIVE) for r in refs), nom
