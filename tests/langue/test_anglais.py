"""Tout ce qu'un utilisateur lit est en anglais (ADR 0039) ; le code, ses commentaires et `docs/plan`
restent en français.

The detector is deliberately simple (accented letters, a word no English label uses, or French
function words) and its false positives are settled by a short list of proper names, never by path
exceptions. Since ADR 0098 it lives in `tools/check_english.py` (`looks_like_shipped_french`); this
module loads that tool by its path (under `--import-mode=importlib` a test module does not import
another one) and keeps the former name as an alias for the guards below.

Ce que la plateforme LIVRE se lit en anglais.

La revue du 2026-10-07 voyait du français dans une console anglaise — « À trier », « Intervention
humaine » — et il venait des gabarits livrés, pas des écrans. Ce test parcourt ce qu'un utilisateur
lit dans ce qui est livré : libellés d'états, descriptions, titres de champs et de tâches,
attestations, instructions et descriptions des agents, skills, et les fichiers déposés dans le dépôt
d'un projet. Les identifiants (noms d'états, de champs, d'objets) restent ce qu'ils sont : ce sont
des clés, pas du texte.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from collections.abc import Callable, Iterator
from types import ModuleType
from typing import Any

import pytest
import yaml

RACINE = pathlib.Path(__file__).resolve().parents[2]


def _check_english() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_english", RACINE / "tools" / "check_english.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # a dataclass needs its module registered
    spec.loader.exec_module(module)
    return module


_CHECK_ENGLISH = _check_english()
ressemble_a_du_francais = _CHECK_ENGLISH.looks_like_shipped_french  # lang: fr-ok alias:internal
_prose: Callable[[str], str] = _CHECK_ENGLISH.shipped_prose
#: Les clés dont la valeur se montre à quelqu'un.
VISIBLES = {
    "display",
    "displayName",
    "display_name",
    "title",
    "description",
    "instructions",
    "attest",
    "justification",
    "motif",
    "message",
    "summary",
}
YAML_LIVRES = [
    *sorted((RACINE / "packages/core/src/choregos_core/dsl/templates").glob("*.yaml")),
    *sorted((RACINE / "packages/core/src/choregos_core/presets").glob("*.yaml")),
    *sorted((RACINE / "templates").rglob("*.yaml")),
    *sorted((RACINE / "demo/workflows").glob("*.yaml")),
    *sorted((RACINE / "packages/core/src/choregos_core/catalogue_d_agents").rglob("*.yaml")),
]
TEXTES_LIVRES = sorted(
    chemin
    for dossier in ("templates", "packages/core/src/choregos_core/catalogue_d_agents")
    for chemin in (RACINE / dossier).rglob("*")
    if chemin.is_file() and chemin.suffix in {".md", ".j2"}
)


def _visibles(valeur: Any, cle: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(valeur, str):
        if cle.rsplit(".", 1)[-1] in VISIBLES:
            yield cle, valeur
    elif isinstance(valeur, dict):
        for sous_cle, sous_valeur in valeur.items():
            yield from _visibles(sous_valeur, f"{cle}.{sous_cle}" if cle else str(sous_cle))
    elif isinstance(valeur, list):
        for element in valeur:
            yield from _visibles(element, cle)


def test_le_detecteur_reconnait_le_francais_et_laisse_passer_l_anglais() -> None:
    assert ressemble_a_du_francais("Intervention humaine")
    assert ressemble_a_du_francais("En cours")
    assert ressemble_a_du_francais("Remettre le badge en main propre")
    assert not ressemble_a_du_francais("Needs a human")
    assert not ressemble_a_du_francais("Hand the badge to its holder in person")
    assert not ressemble_a_du_francais("Léa's Claude Code")


@pytest.mark.parametrize("chemin", YAML_LIVRES, ids=lambda c: str(c.relative_to(RACINE)))
def test_aucun_texte_livre_n_est_en_francais(chemin: pathlib.Path) -> None:
    fautes = [
        f"{cle}: {texte[:80]!r}"
        for document in yaml.safe_load_all(chemin.read_text(encoding="utf-8"))
        for cle, texte in _visibles(document)
        if ressemble_a_du_francais(_prose(texte))
    ]
    assert not fautes, "du français dans ce que la plateforme livre :\n  " + "\n  ".join(fautes)


@pytest.mark.parametrize("chemin", TEXTES_LIVRES, ids=lambda c: str(c.relative_to(RACINE)))
def test_aucun_fichier_depose_ni_skill_n_est_en_francais(chemin: pathlib.Path) -> None:
    prose = _prose(chemin.read_text(encoding="utf-8"))
    fautes = [ligne.strip()[:80] for ligne in prose.splitlines() if ressemble_a_du_francais(ligne)]
    assert not fautes, "du français dans un fichier livré :\n  " + "\n  ".join(fautes)


# ───────────────────── ce que le code DIT : les messages (S21-10 et suivantes) ─────────────────────


def test_les_messages_du_coeur_sont_en_anglais() -> None:
    """Validateur, analyseur, carte, garanties, moteur, éditions typées, politique, secrets (S21-10)."""
    fautes = _CHECK_ENGLISH.french_spoken_strings("packages/core/src/choregos_core")
    assert not fautes, "du français dans ce que le cœur dit :\n  " + "\n  ".join(fautes)


# ──────────────── les contrats : la référence OpenAPI et les JSON Schemas (S21-11) ────────────────

CONTRATS = RACINE / "packages/contracts"


def test_les_contrats_sont_en_anglais() -> None:
    """La référence OpenAPI (résumés, descriptions, réponses, paramètres, schémas), les descriptions
    des JSON Schemas — que l'API sert, que l'éditeur et les types générés montrent — et les messages
    que lèvent les contrats. Les identifiants (chemins, operationId, propriétés, enum) n'y sont pas
    lus : ce sont des clés."""
    documents = [
        (chemin, json.loads(chemin.read_text(encoding="utf-8")))
        for chemin in sorted((CONTRATS / "schemas").glob("*.json"))
    ]
    openapi = CONTRATS / "openapi.yaml"
    documents.append((openapi, yaml.safe_load(openapi.read_text(encoding="utf-8"))))
    fautes = [
        f"{chemin.relative_to(RACINE)}: {cle}: {texte[:80]!r}"
        for chemin, document in documents
        for cle, texte in _visibles(document)
        if ressemble_a_du_francais(_prose(texte))
    ]
    fautes += _CHECK_ENGLISH.french_spoken_strings("packages/contracts/src/choregos_contracts")
    assert not fautes, "du français dans les contrats :\n  " + "\n  ".join(fautes)
