"""`global.extraEnv`: a setting the chart does not know yet, without forking the chart.

To activate, for example, a plugin shipped inactive. Two properties: the variable reaches ALL the
processes that read the platform's settings -- the API, each worker, the migration Job, without
which a plugin activated in the API but not in the migrations would start without its tables -- and a
variable already set by the chart is refused at render time, rather than silently replaced.
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
    """(processes that read the settings, those among them that receive `nom`)."""
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
    assert any("migration" in nom for nom in lecteurs), f"the migration Job is not seen: {lecteurs}"
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
    assert rendu.returncode != 0, "the chart rendered two values for CHOREGOS_DATABASE_URL"
    assert "CHOREGOS_DATABASE_URL" in rendu.stderr


def test_the_env_reference_pattern_reaches_every_process_when_set() -> None:
    """S39-06b: `global.secrets.envPattern` is the allowlist of `env:NAME` references; unset, the
    code's default (`^CONNECTOR_…`) applies and nothing is rendered."""
    rendu = _rendre("global.secrets.envPattern=^(CONNECTOR_|JIRA_)[A-Z0-9_]*$")
    assert rendu.returncode == 0, rendu.stderr[-400:]
    lecteurs, porteurs = _porteurs(rendu.stdout, "CHOREGOS_SECRET_ENV_PATTERN")
    assert lecteurs and porteurs == lecteurs, f"without the variable: {sorted(lecteurs - porteurs)}"
    assert "CHOREGOS_SECRET_ENV_PATTERN" not in _rendre().stdout
