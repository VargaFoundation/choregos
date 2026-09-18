"""Tests du panier jouet."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from panier import Ligne, Panier, total_ttc  # noqa: E402


def panier_exemple() -> Panier:
    panier = Panier()
    panier.ajouter(Ligne("café", 349, 2))
    panier.ajouter(Ligne("filtre", 199))
    return panier


def test_sous_total() -> None:
    assert panier_exemple().sous_total_cents() == 897


def test_total_ttc_arrondi_au_centime() -> None:
    # 897 × 1,20 = 1076,4 → 1076
    assert total_ttc(panier_exemple()) == 1076


def test_panier_vide() -> None:
    assert total_ttc(Panier()) == 0
