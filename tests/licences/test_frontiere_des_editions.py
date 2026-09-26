"""Le cœur reste-t-il indépendant de l'édition entreprise ?

L'[ADR 0024](../../docs/adr/0024-deux-editions.md) pose deux éditions : ce dépôt est le cœur
communautaire, Apache-2.0, et l'édition entreprise vit ailleurs, propriétaire. La frontière ne
tient que si elle est vérifiable : un `import choregos_ee` glissé ici rendrait ce dépôt
inconstructible sans un paquet que personne n'a le droit d'obtenir, et — pire — mêlerait du code
propriétaire à un arbre Apache-2.0.

Le sens de la dépendance est le seul qui vaille : **l'entreprise connaît le cœur, le cœur ne
connaît pas l'entreprise**. C'est ce qui permet à l'édition entreprise de se brancher par les
points d'entrée `choregos.plugins` sans que le cœur ait à savoir qu'elle existe.

Trois propriétés, et la troisième est celle qu'on oublierait : le cœur ne doit pas non plus
mentionner l'édition entreprise dans une chaîne de caractères qu'un `import` dynamique lirait.
"""

from __future__ import annotations

import ast
import pathlib

RACINE = pathlib.Path(__file__).resolve().parents[2]
#: Les arbres de code du cœur — pas les tests, qui peuvent citer l'édition pour l'éprouver.
SOURCES = ("packages", "apps")
#: Les préfixes de module qui n'appartiennent pas au cœur.
INTERDITS = ("choregos_ee", "choregos_enterprise")


def _fichiers() -> list[pathlib.Path]:
    trouves: list[pathlib.Path] = []
    for arbre in SOURCES:
        for chemin in (RACINE / arbre).rglob("*.py"):
            parts = set(chemin.parts)
            if "tests" in parts or "__pycache__" in parts or ".venv" in parts:
                continue
            trouves.append(chemin)
    return sorted(trouves)


def test_il_y_a_bien_du_code_a_verifier() -> None:
    """Sans cette garde, déplacer les paquets rendrait la suite verte et vide."""
    fichiers = _fichiers()
    assert len(fichiers) > 100, f"seulement {len(fichiers)} fichiers trouvés : les chemins ont bougé"


def test_le_coeur_n_importe_jamais_l_edition_entreprise() -> None:
    """Lu par l'AST, pas par une expression régulière : un commentaire ne doit pas faire rougir."""
    fautifs: list[str] = []
    for chemin in _fichiers():
        arbre = ast.parse(chemin.read_text(encoding="utf-8"), filename=str(chemin))
        for noeud in ast.walk(arbre):
            noms: list[str] = []
            if isinstance(noeud, ast.Import):
                noms = [alias.name for alias in noeud.names]
            elif isinstance(noeud, ast.ImportFrom) and noeud.module:
                noms = [noeud.module]
            for nom in noms:
                if any(nom == interdit or nom.startswith(f"{interdit}.") for interdit in INTERDITS):
                    fautifs.append(f"{chemin.relative_to(RACINE)}:{noeud.lineno} → {nom}")
    assert not fautifs, (
        "le cœur importe l'édition entreprise :\n  "
        + "\n  ".join(fautifs)
        + "\nLe sens de la dépendance est l'inverse : l'entreprise se branche par les points "
        "d'entrée `choregos.plugins` (ADR 0024)."
    )


def test_le_coeur_ne_charge_pas_l_edition_par_son_nom() -> None:
    """Un `import_module("choregos_ee")` échapperait au test précédent, qui lit les `import`.

    Cette propriété est la plus facile à contourner par accident : on écrit le nom du module dans
    une chaîne pour « rendre la dépendance optionnelle », et la frontière devient décorative.
    """
    fautifs: list[str] = []
    for chemin in _fichiers():
        texte = chemin.read_text(encoding="utf-8")
        for numero, ligne in enumerate(texte.splitlines(), start=1):
            sans_commentaire = ligne.split("#", 1)[0]
            for interdit in INTERDITS:
                if f'"{interdit}' in sans_commentaire or f"'{interdit}" in sans_commentaire:
                    fautifs.append(f"{chemin.relative_to(RACINE)}:{numero}")
    assert not fautifs, (
        "le cœur nomme l'édition entreprise dans une chaîne — un import dynamique déguisé :\n  "
        + "\n  ".join(fautifs)
    )
