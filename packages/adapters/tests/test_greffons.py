"""Un paquet installé À CÔTÉ peut-il s'enregistrer sans qu'on touche au cœur ?

C'est la question qui décide si une édition entreprise, ou le connecteur maison d'un métier,
peut vivre hors de cet arbre. Jusqu'au 2026-09-26 la réponse était **non** : `register()` était
public, mais rien n'importait jamais un module tiers — il fallait patcher `_register_builtins()`.
`grep entry_points` sur `packages/` et `apps/` ne rendait rien hors des tests.

Ces tests ne simulent pas la découverte : ils écrivent un **vrai** `.dist-info` sur le disque,
l'ajoutent au `sys.path`, et laissent `importlib.metadata` le trouver. Un test qui bouchonnerait
`entry_points()` prouverait que le bouchon marche.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

GREFFON = '''
from choregos_adapters import register


class TrackerMaison:
    """Un tracker qui n'existe pas dans l'arbre de Choregos."""

    def __init__(self, config):
        self.config = config


def brancher():
    register("tracker", "maison")(lambda cfg: TrackerMaison(cfg))
'''

GREFFON_QUI_CASSE = """
def brancher():
    raise RuntimeError("il manque une dépendance")
"""


def _installer(racine: pathlib.Path, nom: str, source: str) -> None:
    """Écrit un module et le `.dist-info` qui le déclare — comme le ferait `pip install`."""
    (racine / f"{nom}.py").write_text(source, encoding="utf-8")
    info = racine / f"{nom}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {nom}\nVersion: 0.1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[choregos.plugins]\n{nom} = {nom}:brancher\n", encoding="utf-8")


@pytest.fixture
def chemin_temporaire(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    sys.path.insert(0, str(tmp_path))
    try:
        yield tmp_path
    finally:
        sys.path.remove(str(tmp_path))
        for module in [m for m in sys.modules if m.startswith("greffon_")]:
            del sys.modules[module]


def test_un_greffon_hors_de_l_arbre_enregistre_son_connecteur(
    chemin_temporaire: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le cœur n'est pas modifié : le paquet se déclare, la plateforme le charge.

    `CHOREGOS_FAKES=0` explicite : la suite de l'API pose `1`, et `build()` rend alors un faux
    quel que soit le type demandé. Sans ce zéro, le test passe seul et rougit en groupe — le
    piège a déjà coûté deux fois aujourd'hui.
    """
    from choregos_adapters import available, build, charger_les_greffons

    monkeypatch.setenv("CHOREGOS_FAKES", "0")

    assert "maison" not in available("tracker"), "le test partirait d'un état déjà pollué"
    _installer(chemin_temporaire, "greffon_exemple", GREFFON)

    charges = charger_les_greffons()
    assert "greffon_exemple" in charges

    adaptateur: Any = build("tracker", "maison", {"url": "https://exemple.test"})
    assert type(adaptateur).__name__ == "TrackerMaison"
    assert adaptateur.config == {"url": "https://exemple.test"}
    assert "maison" in available("tracker")


def test_un_greffon_declare_qui_ne_charge_pas_arrete_tout(chemin_temporaire: pathlib.Path) -> None:
    """Silencieusement absent, il laisserait une plateforme qui paraît complète et ne l'est pas.

    Même règle que `garde.yaml` et que le refus d'un réglage non résolu : quelqu'un l'a installé
    pour qu'il serve. Mieux vaut ne pas démarrer, et nommer le fautif.
    """
    from choregos_adapters import charger_les_greffons

    _installer(chemin_temporaire, "greffon_casse", GREFFON_QUI_CASSE)
    with pytest.raises(RuntimeError, match="greffon_casse") as echec:
        charger_les_greffons()
    assert "dépendance" in str(echec.value), "la cause d'origine doit rester lisible"


def test_sans_greffon_installe_le_chargement_ne_fait_rien() -> None:
    from choregos_adapters import charger_les_greffons

    assert charger_les_greffons("choregos.groupe.qui.n.existe.pas") == []
