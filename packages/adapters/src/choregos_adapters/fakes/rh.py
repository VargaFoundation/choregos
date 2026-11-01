# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_adapters.fakes.hr` (ADR 0098 §3 (a))."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .hr import (
    ANCIENS_OUTILS_DU_FOURNISSEUR,
    FAUX_PAR_FAMILLE,
    OUTILS_DU_FOURNISSEUR,
    FakeBadges,
    FakeFournisseur,
    FakeMdm,
    FakeTransporteur,
    FauxMetier,
    Guichet,
    GuichetDistant,
    serveur_mcp,
)

__all__ = [
    "ANCIENS_OUTILS_DU_FOURNISSEUR",
    "FAUX_PAR_FAMILLE",
    "OUTILS_DU_FOURNISSEUR",
    "FakeBadges",
    "FakeFournisseur",
    "FakeMdm",
    "FakeTransporteur",
    "FauxMetier",
    "Guichet",
    "GuichetDistant",
    "serveur_mcp",
]

replace_module(__name__, sys.modules["choregos_adapters.fakes.hr"])
