"""Les exemples de la documentation valident contre les vrais modèles.

Une documentation fausse coûte plus cher que pas de documentation : elle envoie quelqu'un
dans le mur avec confiance. Les blocs YAML des guides sont donc relus par le parseur du
DSL et par le modèle de politique — les mêmes que la plateforme utilise.

Ce test a déjà servi : le premier jet du guide anglais donnait `ticket_usd: { default: 8 }`,
alors que ce budget est indexé par TAILLE de ticket.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_contracts import Policy
from choregos_core import parse_workflow

DOCS = sorted((Path(__file__).resolve().parents[2] / "docs" / "en").glob("*.md"))
BLOC = re.compile(r"```yaml\n(.*?)```", re.S)


def _blocs() -> list[tuple[str, str, dict[str, Any]]]:
    trouves: list[tuple[str, str, dict[str, Any]]] = []
    for page in DOCS:
        for index, bloc in enumerate(BLOC.findall(page.read_text(encoding="utf-8"))):
            document = yaml.safe_load(bloc)
            if isinstance(document, dict) and document.get("kind") in {"Workflow", "Policy"}:
                trouves.append((f"{page.name}#{index}", bloc, document))
    return trouves


def test_il_y_a_bien_des_exemples_a_verifier() -> None:
    """Sans cette garde, renommer `docs/en` rendrait la suite verte et vide."""
    assert _blocs(), "aucun exemple trouvé dans docs/en : le test ne vérifie plus rien"


@pytest.mark.parametrize(("nom", "bloc", "document"), _blocs(), ids=lambda v: v if isinstance(v, str) else "")
def test_un_exemple_de_la_documentation_est_valide(nom: str, bloc: str, document: dict[str, Any]) -> None:
    if document["kind"] == "Policy":
        Policy.model_validate(document)
        return
    _workflow, rapport = parse_workflow(bloc, strict=False)
    assert rapport.valid, f"{nom} : " + " · ".join(i.format() for i in rapport.errors)
    assert not rapport.warnings, f"{nom} : " + " · ".join(i.format() for i in rapport.warnings)
