"""Modèle de panier : des lignes, un total en centimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

TVA = Decimal("0.20")


@dataclass(frozen=True)
class Ligne:
    """Une ligne de panier. Les prix sont en centimes : pas de flottants."""

    libelle: str
    prix_unitaire_cents: int
    quantite: int = 1

    def total_cents(self) -> int:
        return self.prix_unitaire_cents * self.quantite


@dataclass
class Panier:
    lignes: list[Ligne] = field(default_factory=list)

    def ajouter(self, ligne: Ligne) -> None:
        self.lignes.append(ligne)

    def sous_total_cents(self) -> int:
        return sum(ligne.total_cents() for ligne in self.lignes)


def total_ttc(panier: Panier) -> int:
    """Total TTC en centimes."""
    ht = Decimal(panier.sous_total_cents())
    ttc = ht * (Decimal(1) + TVA)
    return int(ttc)
