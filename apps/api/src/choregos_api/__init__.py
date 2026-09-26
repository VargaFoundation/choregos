"""API Choregos : REST, webhooks, SSE, API interne, OIDC, RLS."""

from __future__ import annotations

from importlib import metadata as _metadata

#: Lue des métadonnées du paquet installé, jamais recopiée : la constante disait `0.1.0` alors
#: que le dépôt était en 0.4.1, et l'API s'annonçait `1.0.0` par-dessus. Trois versions, dont
#: deux fausses. `tools/bump_version.py` n'a désormais rien à toucher ici.
try:
    __version__ = _metadata.version("choregos-api")
except _metadata.PackageNotFoundError:  # arbre non installé (build, outillage)
    __version__ = "0.0.0+inconnue"
