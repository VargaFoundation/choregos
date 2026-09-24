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


def outils_du_projet(autorises: list[str], groupes: list[str] | None = None) -> list[OutilCatalogue]:
    """Ce qu'un projet a le droit d'appeler. Liste vide = rien, et c'est le défaut.

    Un catalogue déployé ne s'ouvre pas à tous les projets par accident : un projet déclare
    ce dont il a besoin, comme il déclare ses connecteurs.
    """
    if not autorises:
        return []
    groupes = groupes or []
    # DEUX verrous, et ils ne disent pas la même chose. Le déploiement dit QUI a le droit
    # (`groups` sur l'outil) ; le projet dit ce dont IL se sert (`tools`). Un projet qui
    # déclare un outil réservé à un groupe dont il ne fait pas partie ne l'obtient pas —
    # sinon la liste du projet deviendrait le seul contrôle, et elle est modifiable par
    # l'équipe du projet.
    ouverts = [o for o in catalogue().outils if o.ouvert_a(groupes)]
    if autorises == ["*"]:
        return ouverts
    permis = set(autorises)
    return [o for o in ouverts if o.name in permis]


async def appeler(outil: OutilCatalogue, arguments: dict[str, Any]) -> tuple[int, Any]:
    """Appelle le fournisseur — HTTP direct ou serveur MCP distant.

    Rend le code et le corps, jamais la clé. Et surtout : **le jeton du run ne sort pas**.
    Il authentifie l'agent auprès de NOUS ; le donner à un service tiers reviendrait à lui
    confier de quoi écrire dans la plateforme. Le distant reçoit `credential_env`, une clé
    qui ne vaut que pour lui.
    """
    if outil.mcp is not None:
        return await _appeler_mcp(outil, arguments)
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


async def _appeler_mcp(outil: OutilCatalogue, arguments: dict[str, Any]) -> tuple[int, Any]:
    """`tools/call` JSON-RPC sur un serveur MCP extérieur à l'organisation.

    Ce qui part : le nom de l'outil CHEZ LUI, les arguments de l'agent, et les en-têtes du
    catalogue — dont la clé du service, jamais la nôtre. L'agent ne choisit ni l'URL ni le
    nom distant : il nomme un outil du catalogue, et l'indirection est ce qui permet de
    n'exposer qu'une partie d'un serveur.
    """
    distant = outil.mcp
    assert distant is not None  # garanti par `appeler`
    cle = os.environ.get(outil.credential_env, "") if outil.credential_env else ""
    entetes = {
        nom: valeur.replace("{{ credential }}", cle).replace("{{credential}}", cle)
        for nom, valeur in distant.headers.items()
    }
    entetes.setdefault("Content-Type", "application/json")
    corps = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": distant.tool, "arguments": arguments},
    }
    async with httpx.AsyncClient() as client:
        reponse = await client.post(distant.url, json=corps, headers=entetes, timeout=distant.timeout_s)
    try:
        charge = reponse.json()
    except ValueError:
        return reponse.status_code, {"text": reponse.text[:20_000]}
    # Une erreur JSON-RPC est une réponse, pas une panne de transport : on la rend telle
    # quelle avec un code parlant, pour que l'agent la lise au lieu de la prendre pour un
    # succès vide.
    if isinstance(charge, dict) and "error" in charge:
        return 502, charge["error"]
    return reponse.status_code, (charge.get("result") if isinstance(charge, dict) else charge)
