"""Le Role du chart couvre-t-il ce que l'exécuteur fait vraiment ?

Deux fois en une journée, un verbe manquant a tué un ticket : `patch` sur les Jobs
(réveiller un Job en file) puis `update` sur les Secrets (remplacer un jeton). À chaque
fois l'API répondait 403, l'exception remontait, et le workflow mourait — sur un message
qui parle de droits, jamais de ce qu'on essayait de faire.

Ce test lit les deux sources et les confronte : les appels HTTP que l'exécuteur écrit, et
les verbes que le chart accorde. Une relecture ne l'a pas vu deux fois ; elle ne le verra
pas la troisième.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

RACINE = Path(__file__).resolve().parents[3]
EXECUTEUR = RACINE / "packages/adapters/src/choregos_adapters/executor/k8s_job.py"
ROLE = RACINE / "charts/choregos/charts/orchestrator/templates/rbac-jobs.yaml"

#: Ce que le verbe HTTP exige comme verbe RBAC. `GET` couvre `get` et `list` ; on exige le
#: plus précis, `get`, parce que c'est celui qu'un appel nominatif consomme.
VERBE = {"GET": "get", "POST": "create", "PUT": "update", "PATCH": "patch", "DELETE": "delete"}

APPEL = re.compile(r'"(GET|POST|PUT|PATCH|DELETE)",\s*\n?\s*f?"[^"]*/(jobs|secrets)')


def _emplois() -> set[tuple[str, str]]:
    source = EXECUTEUR.read_text(encoding="utf-8")
    return {(ressource, VERBE[methode]) for methode, ressource in APPEL.findall(source)}


def _accordes() -> dict[str, set[str]]:
    # Le template est du Helm : on ne garde que le bloc `rules`, qui est du YAML pur.
    texte = ROLE.read_text(encoding="utf-8")
    debut = texte.index("rules:")
    fin = texte.index("---", debut)
    regles = yaml.safe_load(texte[debut:fin])["rules"]
    return {
        ressource: set(regle["verbs"]) for regle in regles for ressource in regle["resources"]
    }


def test_il_y_a_bien_des_appels_a_confronter() -> None:
    """Sans cette garde, renommer une méthode rendrait le test vert et vide."""
    emplois = _emplois()
    assert {"jobs", "secrets"} <= {r for r, _ in emplois}, emplois


@pytest.mark.parametrize(("ressource", "verbe"), sorted(_emplois()))
def test_le_role_accorde_ce_que_l_executeur_emploie(ressource: str, verbe: str) -> None:
    accordes = _accordes().get(ressource, set())
    assert verbe in accordes, (
        f"l'exécuteur fait `{verbe}` sur `{ressource}`, le Role ne l'accorde pas "
        f"(accordés : {sorted(accordes)}). L'API répondra 403 et le ticket mourra."
    )
