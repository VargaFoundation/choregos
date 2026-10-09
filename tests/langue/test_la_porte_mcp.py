"""La porte MCP a un seul nom (#338, ADR 0030 : « une porte MCP »).

La console et la documentation disaient « MCP door », l'API et son contrat « MCP gate » : le même
objet sous deux noms, à côté d'une « garantie » (gate) qui est autre chose. Ce que l'utilisateur lit
dit « door » ; ce test refuse le retour de l'autre nom dans ce que l'API sert, son contrat et la
console.
"""

from __future__ import annotations

import pathlib
import re

RACINE = pathlib.Path(__file__).resolve().parents[2]
LIEUX = [
    "apps/api/src",
    "apps/web/src",
    "packages/contracts/openapi.yaml",
    "packages/contracts/ts",
    "packages/core/src/choregos_core/catalogue_d_agents",
    "templates",
]
SUFFIXES = {".py", ".ts", ".tsx", ".yaml", ".yml", ".md", ".json"}
AUTRE_NOM = re.compile(r"\bMCP gate\b", re.IGNORECASE)


def _fichiers() -> list[pathlib.Path]:
    fichiers: list[pathlib.Path] = []
    for lieu in LIEUX:
        chemin = RACINE / lieu
        if chemin.is_file():
            fichiers.append(chemin)
        else:
            fichiers += [
                f for f in chemin.rglob("*") if f.suffix in SUFFIXES and "node_modules" not in f.parts
            ]
    return fichiers


def test_la_porte_mcp_ne_s_appelle_pas_gate() -> None:
    trouves = [
        f"{f.relative_to(RACINE)}:{n}"
        for f in _fichiers()
        for n, ligne in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
        if AUTRE_NOM.search(ligne)
    ]
    assert trouves == [], trouves


def test_les_lieux_existent() -> None:
    assert all((RACINE / lieu).exists() for lieu in LIEUX)
    assert len(_fichiers()) > 100
