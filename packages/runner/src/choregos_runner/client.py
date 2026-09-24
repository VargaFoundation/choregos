"""Le client de l'API interne vit dans `choregos_tools_mcp.client` ; ce module le réexporte.

Le runner importe le serveur d'outils (pour le démarrer dans son pod) et le serveur
d'outils importait le client du runner : un cycle de paquets, résolu seulement parce que uv
travaille en espace de travail — hors de lui, aucun des deux wheels ne s'installait (état
des lieux du 2026-09-24). Le client est parti chez celui qui en a le plus besoin ; le
runner en dépend, plus l'inverse.
"""

from __future__ import annotations

from choregos_tools_mcp.client import InternalApiError, InternalClient

__all__ = ["InternalApiError", "InternalClient"]
