"""Tout ce qu'un utilisateur lit est en anglais (ADR 0039) ; le code, ses commentaires et `docs/plan`
restent en français.

Le détecteur est volontairement simple — des lettres accentuées, un mot qu'aucun libellé anglais
n'emploie, ou des mots outils français — et ses faux positifs se règlent par une courte liste de noms
propres, pas par des exceptions de chemin. Il est ici, avec les gardes qui s'en servent : sous
`--import-mode=importlib`, un module de test n'en importe pas un autre.

Ce que la plateforme LIVRE se lit en anglais.

La revue du 2026-10-07 voyait du français dans une console anglaise — « À trier », « Intervention
humaine » — et il venait des gabarits livrés, pas des écrans. Ce test parcourt ce qu'un utilisateur
lit dans ce qui est livré : libellés d'états, descriptions, titres de champs et de tâches,
attestations, instructions et descriptions des agents, skills, et les fichiers déposés dans le dépôt
d'un projet. Les identifiants (noms d'états, de champs, d'objets) restent ce qu'ils sont : ce sont
des clés, pas du texte.
"""

from __future__ import annotations

import pathlib
import re
from collections.abc import Iterator
from typing import Any

import pytest
import yaml

#: Les noms propres gardent leurs accents.
NOMS_PROPRES = ("Léa", "Hélène", "Zoé", "Varga")
_ACCENTS = re.compile(r"[àâçéèêëîïôûùüÿœÀÂÇÉÈÊËÎÏÔÛÙÜŸŒ«»]")
_MOTS = re.compile(
    r"\b(les|des|une|est|sont|vers|pour|dans|avec|aux|pas|qui|que|leur|chaque|ne|ou|au|du|sur|par)\b",
    re.IGNORECASE,
)


#: Des mots sans accent qu'aucun libellé anglais n'emploie : « En cours », « Intervention humaine »,
#: « Cadrage » passaient sans eux.
_LEXIQUE = re.compile(
    r"\b(en|cours|ouverte?|humaine?|cadrage|relecture|besoin|demande|poste|compte|groupe|reprise|commande|"
    r"tu|lis|ecris|verifie)\b",
    re.IGNORECASE,
)


def ressemble_a_du_francais(texte: str) -> bool:
    """Vrai pour un accent hors des noms propres, un mot du lexique, ou deux mots outils français."""
    sans_noms = texte
    for nom in NOMS_PROPRES:
        sans_noms = sans_noms.replace(nom, "")
    if _ACCENTS.search(sans_noms) or _LEXIQUE.search(sans_noms):
        return True
    return len(_MOTS.findall(sans_noms)) >= 2


RACINE = pathlib.Path(__file__).resolve().parents[2]
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
]
TEXTES_LIVRES = sorted(
    chemin
    for chemin in (RACINE / "templates").rglob("*")
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


def _prose(texte: str) -> str:
    """Le texte sans ce qui n'est pas de la prose : blocs et extraits de code, gabarits Jinja."""
    texte = re.sub(r"```.*?```", "", texte, flags=re.S)
    texte = re.sub(r"`[^`]*`", "", texte)
    return re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", texte, flags=re.S)


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
