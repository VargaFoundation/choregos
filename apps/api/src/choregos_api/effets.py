# SPDX-License-Identifier: Apache-2.0
"""Les effets des actions gouvernées (ADR 0035) — une couture : `declarer_un_effet`.

Un effet écrit dans un système qui n'est pas le nôtre : un annuaire, un gestionnaire de parc, le
serveur MCP d'un fournisseur. Il est IDEMPOTENT par construction, parce qu'une action se reprend
après une panne : créer un compte le cherche d'abord, ajouter à un groupe tient « déjà membre »
pour un succès. Il ne s'exécute jamais dans la requête qui approuve l'action : l'`ActionWorkflow`
le joue, sous sa clé, et le compense à rebours si une suite échoue.

The core ships `connector.call` — an operation of an organisation connector (ADR 0034) — and
`check` (alias `verifier`), a control with no effect outside; a plugin declares its own. An
effect's parameters are rendered in isolated Jinja, with those of the action (`params`) and the
results of the effects already done (`effects`): an onboarding's second effect adds to the groups
the account the first one created.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db.models import Action, ConnectorOperation, OrgConnector, Project
from .secret_store import resolved_configuration


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
#: La politique de chaque effet d'un greffon (ADR 0034) : `connector.call` n'en a pas — c'est
#: l'OPÉRATION appelée qui en a une, dans l'organisation et le projet.
_POLITIQUES: dict[str, str] = {}


def declarer_un_effet(nom: str, executer: Executer, *, politique: str = "approval") -> None:
    """Appelée par un greffon à son chargement. Le même effet redéclaré passe ; un autre, non.

    `politique` : ce qu'il faut pour qu'une action de workflow le joue — `approval` (une personne
    décide, par défaut), `allowed` (la transition suffit) ou `forbidden`."""
    from choregos_adapters import POLITIQUES

    if politique not in POLITIQUES:
        raise ValueError(f"politique inconnue pour l'effet {nom} : {politique} (connues : {POLITIQUES})")
    if nom in _EFFETS and _EFFETS[nom] is not executer:
        raise ValueError(f"effet déjà déclaré : {nom}")
    _EFFETS[nom] = executer
    _POLITIQUES[nom] = politique


def politique_de_l_effet(nom: str) -> str:
    """Ce qu'un effet de greffon exige ; un effet inconnu est interdit."""
    return _POLITIQUES.get(nom, "forbidden")


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
    from jinja2 import StrictUndefined, TemplateError, UndefinedError
    from jinja2.sandbox import SandboxedEnvironment

    if isinstance(valeur, str):
        if "{{" not in valeur and "{%" not in valeur:
            return valeur
        try:
            return SandboxedEnvironment(undefined=StrictUndefined).from_string(valeur).render(**contexte)
        except UndefinedError as absente:
            raise EffetRefuse(f"parameter not found: {absente}") from absente
        except TemplateError as refus:  # une syntaxe fausse, une sortie du bac à sable
            raise EffetRefuse(f"template refused: {refus}") from refus
    if isinstance(valeur, dict):
        return {cle: rendre(v, contexte) for cle, v in valeur.items()}
    if isinstance(valeur, list):
        return [rendre(v, contexte) for v in valeur]
    return valeur


async def _appel_de_connecteur(ctx: ContexteEffet, params: dict[str, Any]) -> dict[str, Any]:
    """`connector.call` under the organisation's egress allowlist (S39-22): a governed action runs
    for the workers, and a host the organisation has not opened is refused before it is called."""
    from .services.egress import egress_scope_for_org

    async with egress_scope_for_org(
        ctx.session, ctx.project.org_id, "workers", f"connector:{params.get('connector', '')}"
    ):
        return await _appeler_le_connecteur(ctx, params)


async def _appeler_le_connecteur(ctx: ContexteEffet, params: dict[str, Any]) -> dict[str, Any]:
    """`connector.call` : une opération d'un connecteur de l'organisation, clé résolue ICI.

    L'action a été approuvée : une opération `approval` s'exécute donc ; une `forbidden` non."""
    import httpx
    from choregos_adapters import build
    from choregos_adapters.errors import AdapterError, UpstreamError
    from choregos_adapters.mcp import ErreurMcp
    from choregos_contracts import ActionStatus
    from choregos_core.egress import EgressRefusedError

    nom, operation = str(params.get("connector", "")), str(params.get("operation", ""))
    arguments = dict(params.get("arguments") or {})
    # An action nobody approved writes nothing (ADR 0035, ADR 0052): the workflow is started by the
    # decision, but a workflow started by anything else must not find a way to the device.
    approved = {ActionStatus.APPROVED.value, ActionStatus.RUNNING.value, ActionStatus.AWAITING_EVIDENCE.value}
    if ctx.action.status not in approved:
        raise EffetRefuse(
            f"{nom}/{operation}: the action is {ctx.action.status}, not approved — nothing is done"
        )
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
    from .services.connecteurs import politique_pour_le_projet

    # Celle du PROJET : l'organisation, resserrée par le projet, bornée par ses groupes. Une action
    # approuvée joue une opération `approval` ; une opération que ce projet s'interdit, jamais.
    if (
        ligne is None
        or await politique_pour_le_projet(ctx.session, ctx.project, nom, operation) == "forbidden"
    ):
        raise EffetRefuse(f"{nom}/{operation} is forbidden to this project, or unknown")
    if ligne.input_schema:
        import jsonschema

        try:
            jsonschema.validate(arguments, ligne.input_schema)
        except jsonschema.ValidationError as exc:
            raise EffetRefuse(f"{nom}/{operation} : {exc.message}") from exc
    arguments.update(await _arguments_de_la_plateforme(ctx, instance, operation, arguments))
    try:
        config = await resolved_configuration(
            ctx.session, instance.org_id, instance.kind, instance.type, instance.config, instance.secret_refs
        )
        client = build(instance.kind, instance.type, config)
        if hasattr(client, "call_tool"):
            resultat = await client.call_tool(operation, arguments)
            if resultat.get("isError"):
                raise EffetRefuse(f"{nom}/{operation}: the server answered with an error")
            # Ce que l'outil a STRUCTURÉ, quand il le fait : un effet suivant cite `effects[0].serial`
            # comme pour un connecteur typé, pas l'enveloppe du protocole (S20-07).
            structure = resultat.get("structuredContent")
            return dict(structure) if isinstance(structure, dict) else dict(resultat)
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
    except EgressRefusedError as refus:
        raise EffetRefuse(f"{nom}/{operation} : {refus}") from refus  # permanent: no retry
    except httpx.HTTPError:
        raise
    except AdapterError as refus:
        raise EffetRefuse(f"{nom}/{operation} : {refus}") from refus


async def _arguments_de_la_plateforme(
    ctx: ContexteEffet, instance: OrgConnector, operation: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """What the operation's type asks the PLATFORM to fill (ADR 0052), after the agent's arguments
    were checked against the schema — an agent cannot pass them, the schema closes them:
    - `action_key`: the action and the call, hashed — the same on every retry, another for another call;
    - `work_item_key`: the work item the action belongs to, when it has one."""
    import hashlib
    import json

    from choregos_adapters import operation_spec

    declared = operation_spec(instance.kind, instance.type, operation)
    if declared is None or not declared.platform_arguments:
        return {}
    filled: dict[str, Any] = {}
    if "action_key" in declared.platform_arguments:
        canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
        filled["action_key"] = hashlib.sha256(
            f"{ctx.action.id}:{operation}:{canonical}".encode()
        ).hexdigest()[:24]
    if "work_item_key" in declared.platform_arguments and ctx.action.work_item_id:
        from .db.models import WorkItem

        item = await ctx.session.get(WorkItem, ctx.action.work_item_id)
        if item is not None:
            filled["work_item_key"] = item.tracker_key
    return filled


async def _check(_ctx: ContexteEffet, params: dict[str, Any]) -> dict[str, Any]:
    """`check`: a condition, rendered with the results of the previous effects, that must be TRUE;
    otherwise the action fails, named by its `reason`, and what it had done is compensated. No third
    party system is touched: it is a control (a D+1 check, a closing), allowed by default.

    `verifier` (with `motif`) is the same effect under the name it had before S39-11: actions and
    workflows written with it keep running."""
    condition = str(params.get("condition", "")).strip().lower()
    reason = str(params.get("reason") or params.get("motif") or params.get("condition") or "")
    if condition not in {"true", "1", "yes", "oui"}:  # lang: fr-ok - `oui` kept for old workflows
        raise EffetRefuse(f"check failed: {reason}")
    return {"ok": True, "reason": reason, "motif": reason}  # lang: fr-ok - `motif` kept one release


def reinitialiser() -> None:
    """Pour les tests : seuls les effets du cœur restent."""
    _EFFETS.clear()
    _POLITIQUES.clear()
    _EFFETS["connector.call"] = _appel_de_connecteur
    _EFFETS["check"] = _check
    _POLITIQUES["check"] = "allowed"
    _EFFETS["verifier"] = _check  # lang: fr-ok - alias for actions written before S39-11
    _POLITIQUES["verifier"] = "allowed"  # lang: fr-ok - alias for actions written before S39-11


reinitialiser()
