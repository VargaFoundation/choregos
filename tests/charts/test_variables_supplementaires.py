"""`global.extraEnv` : un réglage que le chart ne connaît pas encore, sans fork du chart.

Pour activer, par exemple, un greffon livré inactif. Deux propriétés : la variable arrive à TOUS les
processus qui lisent les réglages de la plateforme — l'API, chaque worker, le Job de migration, sans
quoi un greffon activé dans l'API mais pas dans les migrations démarrerait sans ses tables — et une
variable déjà posée par le chart est refusée au rendu, plutôt que remplacée en silence.
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


def _porteurs(rendu: str, nom: str) -> tuple[set[str], set[str]]:
    """(processus qui lisent les réglages, ceux d'entre eux qui reçoivent `nom`)."""
    lecteurs: set[str] = set()
    porteurs: set[str] = set()
    for doc in yaml.safe_load_all(rendu):
        if not doc or doc.get("kind") not in {"Deployment", "Job"}:
            continue
        gabarit = doc["spec"]["template"]["spec"]
        for conteneur in gabarit.get("containers", []):
            noms = {v["name"] for v in (conteneur.get("env") or [])}
            if "CHOREGOS_ENV" not in noms:
                continue
            lecteurs.add(doc["metadata"]["name"])
            if nom in noms:
                porteurs.add(doc["metadata"]["name"])
    return lecteurs, porteurs


def test_la_variable_arrive_a_chaque_processus_de_la_plateforme() -> None:
    rendu = _rendre("global.extraEnv[0].name=CHOREGOS_ESSAI_ONTOLOGIE", "global.extraEnv[0].value=1")
    assert rendu.returncode == 0, rendu.stderr[-400:]
    lecteurs, porteurs = _porteurs(rendu.stdout, "CHOREGOS_ESSAI_ONTOLOGIE")
    assert any("migration" in nom for nom in lecteurs), f"le Job de migration n'est pas vu : {lecteurs}"
    assert porteurs == lecteurs, f"sans la variable : {sorted(lecteurs - porteurs)}"
    assert 'value: "1"' in rendu.stdout


def test_sans_valeur_rien_n_est_ajoute() -> None:
    rendu = _rendre()
    assert rendu.returncode == 0, rendu.stderr[-400:]
    assert "CHOREGOS_ESSAI_ONTOLOGIE" not in rendu.stdout


def test_une_variable_deja_posee_par_le_chart_est_refusee() -> None:
    rendu = _rendre(
        "global.extraEnv[0].name=CHOREGOS_DATABASE_URL", "global.extraEnv[0].value=postgresql://x"
    )
    assert rendu.returncode != 0, "le chart a rendu deux valeurs pour CHOREGOS_DATABASE_URL"
    assert "CHOREGOS_DATABASE_URL" in rendu.stderr
