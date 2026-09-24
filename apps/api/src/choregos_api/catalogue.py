"""Le catalogue d'outils du déploiement, et l'appel d'un outil pour le compte d'un run.

La clé du fournisseur vit ici, dans l'environnement de l'API — jamais dans le pod d'un
agent. L'agent nomme un outil ; la plateforme fabrique la requête, l'envoie, compte l'appel,
et ne rend que la réponse. Voir `choregos_core.catalogue` pour le pourquoi.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from choregos_core.catalogue import Catalogue, OutilCatalogue, charger_catalogue, construire_requete


@lru_cache(maxsize=1)
def catalogue() -> Catalogue:
    """Le catalogue du déploiement, lu une fois. Absent = aucun outil, pas une erreur."""
    chemin = os.environ.get("CHOREGOS_TOOL_CATALOG", "")
    if not chemin or not Path(chemin).is_file():
        return Catalogue()
    return charger_catalogue(Path(chemin).read_text(encoding="utf-8"))


def vider_cache() -> None:
    catalogue.cache_clear()


def outils_du_projet(autorises: list[str]) -> list[OutilCatalogue]:
    """Ce qu'un projet a le droit d'appeler. Liste vide = rien, et c'est le défaut.

    Un catalogue déployé ne s'ouvre pas à tous les projets par accident : un projet déclare
    ce dont il a besoin, comme il déclare ses connecteurs.
    """
    if not autorises:
        return []
    if autorises == ["*"]:
        return list(catalogue().outils)
    permis = set(autorises)
    return [o for o in catalogue().outils if o.name in permis]


async def appeler(outil: OutilCatalogue, arguments: dict[str, Any]) -> tuple[int, Any]:
    """Appelle le fournisseur. Rend le code HTTP et le corps, jamais la clé."""
    cle = os.environ.get(outil.credential_env, "") if outil.credential_env else None
    requete = construire_requete(outil, arguments, cle)
    async with httpx.AsyncClient() as client:
        reponse = await client.request(
            requete["method"],
            requete["url"],
            params=requete["params"] or None,
            headers=requete["headers"] or None,
            json=requete["json"],
            timeout=requete["timeout"],
        )
    try:
        return reponse.status_code, reponse.json()
    except ValueError:
        # Un fournisseur qui répond du texte n'est pas une panne : on le rend tel quel,
        # tronqué, plutôt que de faire échouer l'étape sur un problème de format.
        return reponse.status_code, {"text": reponse.text[:20_000]}
