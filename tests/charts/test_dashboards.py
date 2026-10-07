"""Les dashboards Grafana du chart : en anglais, et rangeables dans un dossier.

Sur le locataire dev de Diametral (2026-10-05), les six dashboards atterrissaient dans le dossier
`General` de l'org plateforme, mêlés à ceux de l'infrastructure, avec des titres en français :
rien ne permettait au sidecar Grafana de les ranger. `monitoring.dashboardAnnotations` pose
l'annotation que le sidecar lit (`grafana_folder` le plus souvent) sur chaque ConfigMap.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHART = RACINE / "charts" / "choregos"
ACCENTS = re.compile(r"[àâçéèêëîïôûùüÿœ]", re.IGNORECASE)


def _dashboards(*surcharges: str) -> list[dict]:
    commande = ["helm", "template", "choregos", str(CHART)]
    for surcharge in surcharges:
        commande += ["--set", surcharge]
    rendu = subprocess.run(commande, capture_output=True, text=True, check=True)
    return [
        doc
        for doc in yaml.safe_load_all(rendu.stdout)
        if doc
        and doc.get("kind") == "ConfigMap"
        and (doc["metadata"].get("labels") or {}).get("grafana_dashboard")
    ]


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
def test_l_annotation_demandee_est_posee_sur_chaque_dashboard() -> None:
    cms = _dashboards("monitoring.dashboardAnnotations.grafana_folder=choregos")
    assert cms
    assert all((cm["metadata"].get("annotations") or {}).get("grafana_folder") == "choregos" for cm in cms)


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
def test_sans_valeur_aucune_annotation() -> None:
    assert all(not cm["metadata"].get("annotations") for cm in _dashboards())


def _textes(noeud: object, cle: str | None = None):
    if isinstance(noeud, dict):
        for k, v in noeud.items():
            if k != "expr":
                yield from _textes(v, k)
    elif isinstance(noeud, list):
        for v in noeud:
            yield from _textes(v, cle)
    elif isinstance(noeud, str) and cle in {"title", "description", "legendFormat"}:
        yield noeud


@pytest.mark.parametrize("fichier", sorted((CHART / "dashboards").glob("*.json")), ids=lambda p: p.name)
def test_les_dashboards_sont_en_anglais(fichier: pathlib.Path) -> None:
    francais = [t for t in _textes(json.loads(fichier.read_text())) if ACCENTS.search(t)]
    assert not francais, francais
