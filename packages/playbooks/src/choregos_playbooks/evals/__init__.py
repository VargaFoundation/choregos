"""Évals des playbooks : des assertions déterministes, plus un juge pour le reste.

Un prompt dégradé doit faire échouer la CI. Les cas vivent dans `cases/`, chacun décrit
ce qu'il attend du rendu (et, en mode complet, du comportement d'un agent réel).
"""

from __future__ import annotations

from pathlib import Path

CASES_DIR = Path(__file__).parent / "cases"

__all__ = ["CASES_DIR"]
