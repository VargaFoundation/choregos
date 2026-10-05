# SPDX-License-Identifier: Apache-2.0
"""Les effets des actions gouvernées (ADR 0035) — une couture : `declarer_un_effet`.

Un effet écrit dans un système qui n'est pas le nôtre : un annuaire, un gestionnaire de parc, le
serveur MCP d'un fournisseur. Il est IDEMPOTENT par construction, parce qu'une action se reprend
après une panne : créer un compte le cherche d'abord, ajouter à un groupe tient « déjà membre »
pour un succès. Il ne s'exécute jamais dans la requête qui approuve l'action : l'`ActionWorkflow`
le joue, sous sa clé, et le compense à rebours si une suite échoue.

Le cœur livre `connector.call` — une opération d'un connecteur de l'organisation (ADR 0034) ; un
greffon déclare les siens. Les paramètres d'un effet se rendent en Jinja isolé, avec ceux de
l'action (`params`) et les résultats des effets déjà faits (`effects`) : le second effet d'une
arrivée ajoute aux groupes le compte que le premier a créé.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import Action, ConnectorOperation, OrgConnector, Project


class EffetRefuse(Exception):  # noqa: N818 - un refus définitif, rendu tel quel
    """Un refus qu'aucune nouvelle tentative ne changera (un compte hors de l'unité, une opération
    interdite, un paramètre manquant) : l'action échoue et se compense."""


@dataclass(frozen=True)
class ContexteEffet:
    session: AsyncSession
    action: Action
    project: Project


Executer = Callable[[ContexteEffet, dict[str, Any]], Awaitable[dict[str, Any]]]

_EFFETS: dict[str, Executer] = {}


def declarer_un_effet(nom: str, executer: Executer) -> None:
    """Appelée par un greffon à son chargement. Le même effet redéclaré passe ; un autre, non."""
    if nom in _EFFETS and _EFFETS[nom] is not executer:
        raise ValueError(f"effet déjà déclaré : {nom}")
    _EFFETS[nom] = executer


def effet(nom: str) -> Executer:
    try:
        return _EFFETS[nom]
    except KeyError:
        raise EffetRefuse(f"effet inconnu : {nom} (connus : {', '.join(sorted(_EFFETS))})") from None


def effets_declares() -> frozenset[str]:
    return frozenset(_EFFETS)


def rendre(valeur: Any, contexte: dict[str, Any]) -> Any:
    """Les chaînes `{{ … }}` rendues en Jinja ISOLÉ, récursivement ; une variable absente est une
    erreur, pas une chaîne vide — un compte sans UPN ne doit pas partir."""
    from jinja2 import StrictUndefined, UndefinedError
    from jinja2.sandbox import SandboxedEnvironment

    if isinstance(valeur, str):
        if "{{" not in valeur and "{%" not in valeur:
            return valeur
        try:
            return SandboxedEnvironment(undefined=StrictUndefined).from_string(valeur).render(**contexte)
        except UndefinedError as absente:
            raise EffetRefuse(f"paramètre introuvable : {absente}") from absente
    if isinstance(valeur, dict):
        return {cle: rendre(v, contexte) for cle, v in valeur.items()}
    if isinstance(valeur, list):
        return [rendre(v, contexte) for v in valeur]
    return valeur


async def _appel_de_connecteur(ctx: ContexteEffet, params: dict[str, Any]) -> dict[str, Any]:
    """`connector.call` : une opération d'un connecteur de l'organisation, clé résolue ICI.

    L'action a été approuvée : une opération `approval` s'exécute donc ; une `forbidden` non."""
    import httpx
    from choregos_adapters import build, configuration_resolue
    from choregos_adapters.errors import AdapterError, UpstreamError
    from choregos_adapters.mcp import ErreurMcp

    nom, operation = str(params.get("connector", "")), str(params.get("operation", ""))
    arguments = dict(params.get("arguments") or {})
    instance = (
        await ctx.session.execute(
            select(OrgConnector).where(OrgConnector.org_id == ctx.project.org_id, OrgConnector.name == nom)
        )
    ).scalar_one_or_none()
    if instance is None:
        raise EffetRefuse(f"aucun connecteur {nom} dans l'organisation")
    ligne = (
        await ctx.session.execute(
            select(ConnectorOperation).where(
                ConnectorOperation.connector_id == instance.id, ConnectorOperation.name == operation
            )
        )
    ).scalar_one_or_none()
    if ligne is None or ligne.policy == "forbidden":
        raise EffetRefuse(f"{nom}/{operation} est interdite ou inconnue")
    if ligne.input_schema:
        import jsonschema

        try:
            jsonschema.validate(arguments, ligne.input_schema)
        except jsonschema.ValidationError as exc:
            raise EffetRefuse(f"{nom}/{operation} : {exc.message}") from exc
    try:
        config = configuration_resolue(instance.kind, instance.type, instance.config, instance.secret_refs)
        client = build(instance.kind, instance.type, config)
        if hasattr(client, "call_tool"):
            resultat = await client.call_tool(operation, arguments)
            if resultat.get("isError"):
                raise EffetRefuse(f"{nom}/{operation} : le serveur a répondu une erreur")
            return dict(resultat)
        return dict(await client.executer(operation, arguments))
    except UpstreamError as panne:
        code = panne.status_code or 0
        if code in {0, 429} or code >= 500:
            raise  # passager : Temporal retente
        raise EffetRefuse(f"{nom}/{operation} : {panne}") from panne
    except ErreurMcp as erreur:
        if erreur.code in {401, 403}:
            raise EffetRefuse(f"{nom}/{operation} : {erreur}") from erreur
        raise
    except httpx.HTTPError:
        raise
    except AdapterError as refus:
        raise EffetRefuse(f"{nom}/{operation} : {refus}") from refus


def reinitialiser() -> None:
    """Pour les tests : seuls les effets du cœur restent."""
    _EFFETS.clear()
    _EFFETS["connector.call"] = _appel_de_connecteur


reinitialiser()
