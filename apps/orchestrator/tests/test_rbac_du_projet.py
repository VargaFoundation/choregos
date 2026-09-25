"""Le Role d'un namespace de projet accorde-t-il ce que l'exécuteur fait vraiment ?

`packages/adapters/tests/test_rbac_du_job.py` confronte l'exécuteur au Role du **chart** —
celui du namespace de la plateforme, où tourne le banc. Un projet provisionné reçoit un
autre Role, rendu par `gitops.py` dans SON namespace de runners, et celui-là n'avait ni
`patch` sur les Jobs ni `update`/`patch` sur les Secrets : la file d'admission y aurait
échoué en silence (une admission ratée se tait, par choix) et un run rejoué serait parti
avec un jeton périmé. Même méthode, même exigence, sur la deuxième source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core import load_preset
from choregos_orchestrator.gitops import render_project_manifests

RACINE = Path(__file__).resolve().parents[3]
EXECUTEUR = RACINE / "packages/adapters/src/choregos_adapters/executor/k8s_job.py"
VERBE = {"GET": "get", "POST": "create", "PUT": "update", "PATCH": "patch", "DELETE": "delete"}
APPEL = re.compile(r'"(GET|POST|PUT|PATCH|DELETE)",\s*\n?\s*f?"[^"]*/(jobs|secrets)')


def _emplois() -> set[tuple[str, str]]:
    source = EXECUTEUR.read_text(encoding="utf-8")
    return {(ressource, VERBE[methode]) for methode, ressource in APPEL.findall(source)}


def _role_du_projet() -> dict[str, set[str]]:
    config = ProjectConfig(
        slug="billing", org="varga", repo=RepoConfig(url="https://github.com/varga/billing.git")
    )
    files = render_project_manifests("billing", config, load_preset("team"))
    roles = [
        doc
        for texte in files.values()
        for doc in yaml.safe_load_all(texte)
        if doc and doc.get("kind") == "Role" and doc["metadata"]["name"] == "choregos-executor"
    ]
    assert len(roles) == 1, "un seul Role `choregos-executor` par projet"
    return {ressource: set(regle["verbs"]) for regle in roles[0]["rules"] for ressource in regle["resources"]}


def test_il_y_a_bien_des_appels_a_confronter() -> None:
    assert {"jobs", "secrets"} <= {r for r, _ in _emplois()}


@pytest.mark.parametrize(("ressource", "verbe"), sorted(_emplois()))
def test_le_role_du_projet_accorde_ce_que_l_executeur_emploie(ressource: str, verbe: str) -> None:
    accordes = _role_du_projet().get(ressource, set())
    assert verbe in accordes, (
        f"l'exécuteur fait `{verbe}` sur `{ressource}`, le Role du projet ne l'accorde pas "
        f"(accordés : {sorted(accordes)}). Dans un namespace de projet, l'API répondrait 403."
    )
