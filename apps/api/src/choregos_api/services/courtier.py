# SPDX-License-Identifier: Apache-2.0
"""Le courtier (ADR 0034) : ce qu'un run atteint des connecteurs de l'organisation, et comment.

Un agent ne reçoit jamais la clé d'un serveur MCP ni son adresse : il nomme un outil
(`<connecteur>__<opération>`), le side-car `choregos-tools` le transmet à `/internal`, et la
plateforme fait l'appel avec la clé DU CONNECTEUR, résolue ici. Le jeton du run reste l'unique
justificatif dans le pod ; la clé du serveur atteint le serveur, jamais le pod.

Ce qu'un run voit est l'intersection de quatre décisions :
- la SÉLECTION de son agent : la version nomme ses serveurs (`mcp_servers`) et, au besoin, des
  motifs d'outils (ADR 0033) — un agent qui ne nomme pas un connecteur n'en voit rien ;
- l'ACTIVATION par l'organisation : la politique effective doit être `allowed` — `approval` passe
  par une action gouvernée (ADR 0035), `forbidden` n'existe pas pour le run ;
- les GROUPES de l'opération, qui doivent croiser ceux du projet (aucun : tous) ;
- ce que le PROJET resserre.
"""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AgentVersion,
    ConnectorOperation,
    OrgConnector,
    Project,
    ProjectOperationPolicy,
    Run,
)
from .connecteurs import plus_stricte

SEPARATEUR = "__"


@dataclass(frozen=True)
class OutilDuCourtier:
    nom: str
    connecteur: OrgConnector
    operation: ConnectorOperation
    #: `approval` : l'appel PROPOSE une action gouvernée (ADR 0035) au lieu d'atteindre le serveur.
    sous_validation: bool = False

    def annonce(self) -> dict[str, Any]:
        """Le format MCP que le side-car sert à l'agent."""
        description = self.operation.description or self.operation.name
        if self.sous_validation:
            description += " — needs a human approval: calling it proposes the action, it does not run it"
        return {
            "name": self.nom,
            "description": f"[{self.connecteur.name}] {description}",
            "inputSchema": self.operation.input_schema or {"type": "object"},
        }


async def _selection(session: AsyncSession, run: Run) -> dict[str, list[str]]:
    """{connecteur: motifs} que la version de l'agent du run nomme ; rien sans agent du registre."""
    if not run.agent_slug or not run.agent_version:
        return {}
    from ..db.models import Agent

    spec = (
        await session.execute(
            select(AgentVersion.spec)
            .join(Agent, Agent.id == AgentVersion.agent_id)
            .where(Agent.slug == run.agent_slug, AgentVersion.version == run.agent_version)
        )
    ).scalar_one_or_none()
    # Comme pour la porte (S18-06) : un serveur nommé sans motif n'ouvre RIEN ; tout, c'est `*`,
    # écrit — une version d'agent dit ce qu'elle prend, elle ne le prend pas par omission.
    selection: dict[str, list[str]] = {}
    for serveur in (spec or {}).get("mcp_servers") or []:
        if isinstance(serveur, dict) and serveur.get("connector"):
            selection[str(serveur["connector"])] = [str(m) for m in serveur.get("tools") or []]
    return selection


async def outils_du_courtier(session: AsyncSession, run: Run, project: Project) -> dict[str, OutilDuCourtier]:
    selection = await _selection(session, run)
    if not selection:
        return {}
    groupes = {str(g) for g in ((project.config or {}).get("groups") or [])}
    lignes = (
        await session.execute(
            select(ConnectorOperation, OrgConnector)
            .join(OrgConnector, OrgConnector.id == ConnectorOperation.connector_id)
            .where(OrgConnector.org_id == project.org_id, OrgConnector.name.in_(list(selection)))
        )
    ).all()
    resserres = {
        p.operation_id: p.policy
        for p in (
            await session.execute(
                select(ProjectOperationPolicy).where(ProjectOperationPolicy.project_id == project.id)
            )
        ).scalars()
    }
    outils: dict[str, OutilDuCourtier] = {}
    for operation, connecteur in lignes:
        effective = plus_stricte(operation.policy, resserres.get(operation.id))
        if effective == "forbidden":
            continue
        if operation.groups and not groupes & set(operation.groups):
            continue
        if not any(fnmatchcase(operation.name, m) for m in selection.get(connecteur.name, [])):
            continue
        nom = f"{connecteur.name}{SEPARATEUR}{operation.name}"
        outils[nom] = OutilDuCourtier(nom, connecteur, operation, sous_validation=effective == "approval")
    return outils


async def proposer_l_appel(
    session: AsyncSession, run: Run, project: Project, outil: OutilDuCourtier, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Un outil sous validation, appelé : une action gouvernée, proposée au nom de l'agent du run
    (`agent:<slug>`). Rien n'atteint le serveur avant qu'un humain ré-authentifié l'approuve —
    et qui possède l'agent ne l'approuve pas (séparation des rôles)."""
    from choregos_contracts import ActionOrigin

    from ..rbac import SYSTEM
    from ..schemas.actions import ActionCreate, ActionEffectSpec
    from .actions import proposer

    corps = ActionCreate(
        kind=f"{outil.connecteur.name}.{outil.operation.name}",
        title=f"{outil.operation.description or outil.operation.name} ({outil.connecteur.name})",
        justification=f"proposed by the agent {run.agent_slug} in run {run.id}",
        params={"arguments": arguments},
        effects=[
            ActionEffectSpec.model_validate(
                {
                    "effect": "connector.call",
                    "with": {
                        "connector": outil.connecteur.name,
                        "operation": outil.operation.name,
                        "arguments": arguments,
                    },
                }
            )
        ],
        work_item_id=run.work_item_id,
    )
    action = await proposer(
        session,
        project,
        corps,
        origine=ActionOrigin.TOOL.value,
        propose_par={"kind": "agent", "id": f"agent:{run.agent_slug}", "run_id": run.id},
        principal=SYSTEM,
        run_id=run.id,
    )
    return {
        "action": action.id,
        "status": action.status,
        "message": "proposed: a person approves it in the console before anything is done",
        "decision_path": f"/p/{project.slug}/actions/{action.id}",
    }


class ArgumentsRefuses(ValueError):  # noqa: N818 - un refus motivé, rendu en 400
    pass


def verifier_les_arguments(outil: OutilDuCourtier, arguments: dict[str, Any]) -> None:
    """Le schéma annoncé est appliqué, AVANT l'appel : un fournisseur payant ne reçoit pas un
    appel malformé, et le registre ne le compte pas."""
    if not outil.operation.input_schema:
        return
    import jsonschema

    try:
        jsonschema.validate(arguments, outil.operation.input_schema)
    except jsonschema.ValidationError as exc:
        chemin = "/".join(str(p) for p in exc.absolute_path) or "(racine)"
        raise ArgumentsRefuses(
            f"argument refusé par le schéma de {outil.nom} en {chemin} : {exc.message}"
        ) from exc


async def appeler(outil: OutilDuCourtier, arguments: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """`tools/call` sur le serveur, avec la clé du connecteur résolue ICI — jamais dans le pod."""
    import httpx
    from choregos_adapters import build, configuration_resolue
    from choregos_adapters.errors import AdapterError
    from choregos_adapters.mcp import ErreurMcp
    from choregos_core.secrets import SecretIntrouvable

    instance = outil.connecteur
    try:
        config = configuration_resolue(instance.kind, instance.type, instance.config, instance.secret_refs)
        client = build(instance.kind, instance.type, config)
        if hasattr(client, "call_tool"):
            resultat = await client.call_tool(outil.operation.name, arguments)
        else:
            # Un connecteur qui n'est pas MCP (un annuaire) : son opération déclarée, par son nom,
            # rendue au format d'un résultat MCP — l'agent lit la même chose partout.
            brut = await client.executer(outil.operation.name, arguments)
            resultat = {"content": [{"type": "text", "text": _json(brut)}], "structuredContent": brut}
    except ErreurMcp as erreur:
        return 502, {"error": str(erreur)}
    except AdapterError as refus:
        return 422, {"error": str(refus)}
    except (SecretIntrouvable, httpx.HTTPError) as panne:
        return 502, {"error": f"{instance.name} injoignable : {panne}"}
    return (200 if not resultat.get("isError") else 422), resultat


def _json(valeur: Any) -> str:
    import json

    return json.dumps(valeur, ensure_ascii=False, default=str)


def texte_du_resultat(resultat: dict[str, Any]) -> str:
    """Ce que l'agent lira : les blocs texte d'un résultat MCP."""
    blocs = resultat.get("content") or []
    return "\n".join(str(b.get("text", "")) for b in blocs if isinstance(b, dict) and b.get("type") == "text")
