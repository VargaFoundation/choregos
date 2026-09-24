"""Catalogue d'outils : des API tierces utilisables par un agent, sans lui donner de clé.

POURQUOI CE FICHIER EXISTE
--------------------------
La démonstration RH du 2026-09-23 s'est arrêtée sur « aucune base de profils candidats
accessible ». Ce n'était pas un défaut du moteur : c'était une absence de **données**. Un
agent qui instruit un dossier, cherche un profil ou vérifie une entreprise a besoin d'API
tierces — et ces API ont des clés.

Le réflexe serait de poser la clé dans le pod de l'agent. AGENTS.md l'interdit, et à raison :
un agent qui porte une clé d'API peut la dépenser sans plafond, l'exfiltrer dans un commit,
ou la garder. Le catalogue prend le problème par l'autre bout, exactement comme la passerelle
LLM le fait pour les modèles :

- le **déploiement** déclare les outils et détient les clés ;
- l'agent appelle l'outil **par son nom**, avec son jeton de run pour toute identité ;
- la plateforme appelle le fournisseur, injecte la clé côté serveur, et **compte l'appel**.

L'agent ne voit jamais la clé, ne sort jamais vers le fournisseur (son egress reste fermé),
et sa dépense est plafonnée par run comme le reste.

CE QUE CE N'EST PAS
-------------------
Pas un annuaire de milliers d'outils. Un catalogue est une liste **courte et choisie**, écrite
par celui qui déploie, revue comme du code. Le jour où l'on veut la largeur d'un annuaire
public, c'est un outil du catalogue qui y mène — pas une dépendance de la plateforme.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ChoregosError

GABARIT = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppelHttp(Model):
    """Comment l'appel part chez le fournisseur.

    Les gabarits `{{ champ }}` sont remplis avec les arguments de l'agent, et **seulement**
    avec eux : pas d'URL libre, pas de méthode libre. Un outil du catalogue ne peut pas
    servir à joindre n'importe quoi — sinon le catalogue deviendrait un proxy ouvert au nom
    de la plateforme.
    """

    method: Literal["GET", "POST"] = "GET"
    url: str
    query: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(default=20.0, gt=0, le=120)


class AppelMcp(Model):
    """Un outil servi par un serveur MCP **extérieur à l'organisation**.

    LE JETON DU RUN NE SORT JAMAIS. Il authentifie l'agent auprès de NOUS, et rien d'autre :
    le donner à un service distant reviendrait à lui confier de quoi écrire dans la
    plateforme — poster un résultat, déposer un finding, demander une extension de
    périmètre. Le serveur distant reçoit `credential_env`, une clé qui ne vaut que pour lui.

    `tool` est le nom de l'outil CHEZ LUI ; le nom exposé à l'agent est celui du catalogue.
    Cette indirection n'est pas cosmétique : elle permet de n'exposer qu'une partie des
    outils d'un serveur, et de les renommer dans le vocabulaire de la maison.
    """

    url: str
    #: L'outil tel que le serveur distant le nomme.
    tool: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = Field(default=30.0, gt=0, le=120)


class OutilCatalogue(Model):
    """Un outil : ce que l'agent voit, et ce que la plateforme fait pour lui."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    description: str
    provider: str
    categories: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    #: Groupes de l'organisation autorisés à s'en servir. **Vide = aucune restriction de
    #: groupe** — l'outil reste soumis à la déclaration du projet (`ProjectConfig.tools`).
    #: Deux verrous, et ils ne disent pas la même chose : le déploiement dit QUI a le droit
    #: (groupes), le projet dit ce dont IL se sert (liste d'outils). Un projet qui déclare
    #: un outil réservé à un groupe dont il ne fait pas partie ne l'obtient pas.
    groups: list[str] = Field(default_factory=list)
    #: L'une OU l'autre : un appel HTTP direct, ou un outil d'un serveur MCP distant.
    http: AppelHttp | None = None
    mcp: AppelMcp | None = None
    #: Nom de la variable d'environnement qui porte la clé du fournisseur, côté plateforme.
    #: Jamais la clé elle-même : ce fichier est lu dans une PR.
    credential_env: str | None = None
    #: Ce qu'un appel coûte, en euros. Zéro pour une API gratuite — mais le compter quand
    #: même, parce qu'un quota gratuit s'épuise aussi.
    price_eur: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _une_seule_source(self) -> OutilCatalogue:
        """Un outil a UNE source : un appel HTTP, ou un outil MCP distant.

        Aucune des deux et l'outil ne fait rien ; les deux et on ne saurait laquelle
        employer. Le dire au chargement du catalogue vaut mieux qu'au premier appel.
        """
        if (self.http is None) == (self.mcp is None):
            raise ValueError(f"l'outil {self.name} doit déclarer `http:` ou `mcp:`, et un seul")
        return self

    def ouvert_a(self, groupes: list[str]) -> bool:
        """L'outil est-il ouvert à ces groupes ? Sans restriction déclarée, oui."""
        return not self.groups or bool(set(self.groups) & set(groupes))


class Catalogue(Model):
    outils: list[OutilCatalogue] = Field(default_factory=list)

    def par_nom(self, nom: str) -> OutilCatalogue | None:
        return next((o for o in self.outils if o.name == nom), None)


class OutilInconnuError(ChoregosError):
    """Nom absent du catalogue, ou refusé au projet."""


class ArgumentRefuseError(ChoregosError):
    """Un gabarit réclame un argument que l'agent n'a pas donné."""


def _remplir(gabarit: str, arguments: dict[str, Any]) -> str:
    def _un(m: re.Match[str]) -> str:
        cle = m.group(1)
        if cle not in arguments:
            raise ArgumentRefuseError(f"argument manquant : {cle}")
        return str(arguments[cle])

    return GABARIT.sub(_un, gabarit)


def construire_requete(outil: OutilCatalogue, arguments: dict[str, Any], cle: str | None) -> dict[str, Any]:
    """Rend de quoi appeler le fournisseur : méthode, URL, paramètres, en-têtes, corps.

    La clé n'apparaît que dans les en-têtes rendus ici, jamais dans ce qui est journalisé
    ni dans ce qui repart vers l'agent.
    """
    if outil.http is None:
        raise ValueError(f"l'outil {outil.name} n'a pas de source HTTP")
    entetes = {
        nom: _remplir(valeur, {**arguments, "credential": cle or ""})
        for nom, valeur in outil.http.headers.items()
    }
    return {
        "method": outil.http.method,
        "url": _remplir(outil.http.url, arguments),
        "params": {nom: _remplir(v, arguments) for nom, v in outil.http.query.items()},
        "headers": entetes,
        "json": _remplir_profond(outil.http.body, arguments) if outil.http.body else None,
        "timeout": outil.http.timeout_s,
    }


def _remplir_profond(valeur: Any, arguments: dict[str, Any]) -> Any:
    if isinstance(valeur, str):
        return _remplir(valeur, arguments)
    if isinstance(valeur, dict):
        return {k: _remplir_profond(v, arguments) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [_remplir_profond(v, arguments) for v in valeur]
    return valeur


def charger_catalogue(texte: str) -> Catalogue:
    """Lit un catalogue YAML. Un catalogue illisible est une erreur, pas un catalogue vide."""
    import yaml

    donnees = yaml.safe_load(texte) or {}
    if isinstance(donnees, list):
        donnees = {"outils": donnees}
    return Catalogue.model_validate(donnees)


__all__ = [
    "AppelHttp",
    "AppelMcp",
    "ArgumentRefuseError",
    "Catalogue",
    "OutilCatalogue",
    "OutilInconnuError",
    "charger_catalogue",
    "construire_requete",
]
