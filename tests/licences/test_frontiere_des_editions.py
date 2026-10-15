"""Does the core stay independent of the enterprise edition?

[ADR 0024](../../docs/adr/0024-deux-editions.md) sets two editions: this repository is the
community core, Apache-2.0, and the enterprise edition lives elsewhere, proprietary. The boundary
holds only if it is verifiable: an `import choregos_ee` slipped in here would make this repository
unbuildable without a package nobody has the right to obtain, and — worse — would mix proprietary
code into an Apache-2.0 tree.

The direction of the dependency is the only one that works: **the enterprise knows the core, the
core does not know the enterprise**. That is what lets the enterprise edition plug in through the
`choregos.plugins` entry points without the core having to know it exists.

Three properties, and the third is the one that would be forgotten: the core must not mention the
enterprise edition in a string that a dynamic `import` would read either.
"""

from __future__ import annotations

import ast
import pathlib

RACINE = pathlib.Path(__file__).resolve().parents[2]
#: The core's code trees — not the tests, which may name the edition to exercise it.
SOURCES = ("packages", "apps")
#: The module prefixes that do not belong to the core.
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
    """Without this guard, moving the packages would make the suite green and empty."""
    fichiers = _fichiers()
    assert len(fichiers) > 100, f"only {len(fichiers)} files found: the paths have moved"


def test_le_coeur_n_importe_jamais_l_edition_entreprise() -> None:
    """Read through the AST, not a regular expression: a comment must not turn it red."""
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
        "the core imports the enterprise edition:\n  "
        + "\n  ".join(fautifs)
        + "\nThe direction of the dependency is the reverse: the enterprise plugs in through the "
        "`choregos.plugins` entry points (ADR 0024)."
    )


def test_le_coeur_ne_charge_pas_l_edition_par_son_nom() -> None:
    """An `import_module("choregos_ee")` would escape the previous test, which reads the `import`s.

    This property is the easiest to bypass by accident: the module name is written in a string to
    "make the dependency optional", and the boundary becomes decorative.
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
        "the core names the enterprise edition in a string — a disguised dynamic import:\n  "
        + "\n  ".join(fautifs)
    )
