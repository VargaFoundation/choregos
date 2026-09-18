"""Orchestrateur Choregos : workflows Temporal et activités.

L'orchestrateur planifie et n'appelle jamais un modèle : il décide quelle étape lancer,
le runner l'exécute. Toute la logique de décision vient de `choregos_core`.
"""

from __future__ import annotations

__version__ = "0.1.0"
