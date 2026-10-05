"""Un seul constructeur de ticket : `services.routage.nouveau_ticket` (ADR 0031).

Cinq chemins créaient un ticket, et deux posaient l'état `inbox` à la main : sur un workflow qui
commence ailleurs, le ticket mourait à sa naissance (#173). Le constructeur unique pose l'état
initial du workflow choisi et l'épingle de sa version ensemble ; cette garde refuse qu'un sixième
chemin le contourne.
"""

from __future__ import annotations

import ast
import pathlib

RACINE = pathlib.Path(__file__).resolve().parents[3]
PERMIS = {RACINE / "apps/api/src/choregos_api/services/routage.py"}


def test_aucun_work_item_construit_hors_de_nouveau_ticket() -> None:
    fautifs = []
    for dossier in [RACINE / "apps", RACINE / "packages"]:
        for fichier in dossier.glob("*/src/**/*.py"):
            if fichier in PERMIS:
                continue
            for noeud in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
                if isinstance(noeud, ast.Call) and getattr(noeud.func, "id", None) == "WorkItem":
                    fautifs.append(f"{fichier.relative_to(RACINE)}:{noeud.lineno}")
    assert not fautifs, f"WorkItem( hors de nouveau_ticket : {fautifs}"
