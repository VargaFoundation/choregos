"""L'index des ADR liste-t-il TOUTES les décisions, avec le bon statut ?

`docs/adr/README.md` portait deux tableaux. Le premier, tenu à la main, s'arrêtait à 0014 :
il annonçait donc à un lecteur que les neuf décisions suivantes n'existaient pas — dont celles
qui portent l'attestation de paternité, l'exécuteur `agent-sandbox` et SPIFFE. Personne ne l'a
vu parce que rien ne comparait le dossier à son index.

Deux propriétés : tout fichier du dossier est dans l'index, et le statut de l'index est celui
que la décision elle-même déclare. La seconde attrape le cas plus sournois : une ADR passée de
`proposed` à `accepted` dans son fichier, et restée `proposed` dans l'index.
"""

from __future__ import annotations

import pathlib
import re

ADR = pathlib.Path(__file__).resolve().parents[2] / "docs" / "adr"
INDEX = ADR / "README.md"
#: `| [0015](0015-….md) | résumé | proposed |`
LIGNE = re.compile(r"^\|\s*\[(\d{4})\]\(([^)]+)\)\s*\|[^|]*\|\s*([a-z ]+?)\s*\|", re.M)
#: `- **Status**: proposed, 2026-09-25 — …`
STATUT = re.compile(r"^-\s+\*\*Status\*\*:\s*([a-z]+)", re.M)


def _index() -> dict[str, tuple[str, str]]:
    """{numéro: (fichier lié, statut annoncé)}."""
    return {num: (fichier, statut) for num, fichier, statut in LIGNE.findall(INDEX.read_text("utf-8"))}


def _fichiers() -> dict[str, pathlib.Path]:
    return {chemin.name[:4]: chemin for chemin in sorted(ADR.glob("[0-9][0-9][0-9][0-9]-*.md"))}


def test_l_index_n_oublie_aucune_decision() -> None:
    fichiers, index = _fichiers(), _index()
    assert fichiers, "aucune ADR trouvée : le test ne vérifie plus rien"
    manquantes = sorted(set(fichiers) - set(index))
    assert not manquantes, f"décisions absentes de l'index : {manquantes}"
    fantomes = sorted(set(index) - set(fichiers))
    assert not fantomes, f"l'index cite des décisions qui n'existent pas : {fantomes}"


def test_le_lien_de_l_index_pointe_le_bon_fichier() -> None:
    fichiers, index = _fichiers(), _index()
    faux = [num for num, (lien, _) in index.items() if lien != fichiers[num].name]
    assert not faux, f"liens qui ne pointent pas le fichier de la décision : {faux}"


def test_le_statut_de_l_index_est_celui_de_la_decision() -> None:
    """Une décision acceptée dans son fichier et « proposed » dans l'index est un mensonge lisible."""
    ecarts = []
    for num, (_, annonce) in _index().items():
        texte = _fichiers()[num].read_text("utf-8")
        reel = STATUT.search(texte)
        assert reel, f"la décision {num} ne déclare pas de statut"
        if reel.group(1) != annonce:
            ecarts.append(f"{num}: index={annonce!r}, fichier={reel.group(1)!r}")
    assert not ecarts, "l'index et les décisions ne disent pas la même chose :\n  " + "\n  ".join(ecarts)
