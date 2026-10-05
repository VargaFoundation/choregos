# SPDX-License-Identifier: Apache-2.0
"""Le registre d'agents, côté service (ADR 0033) : ce qu'une épingle de projet peut changer d'une
version, la version effective, et la résolution d'une référence `slug[@version]` pour un run."""

from __future__ import annotations

from dataclasses import dataclass

from choregos_core import utcnow
from choregos_core.instructions import erreurs_des_instructions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, AgentVersion, ProjectAgent
from ..deps import _aware
from ..errors import unprocessable
from ..schemas import AgentOverrides, AgentSpec


def erreurs_d_une_version(spec: AgentSpec) -> None:
    """Des instructions qui ne rendraient pas — syntaxe, variable inconnue, ou accès refusé par le
    bac à sable — sont refusées à la publication, pas découvertes au premier run."""
    erreurs = erreurs_des_instructions(spec.instructions)
    if erreurs:
        raise unprocessable(
            "les instructions de l'agent ne se rendent pas",
            [{"loc": ["instructions"], "msg": e} for e in erreurs],
        )


def _outils(spec: AgentSpec) -> set[str]:
    return {outil for serveur in spec.mcp_servers for outil in serveur.tools}


def elargissements(spec: AgentSpec, surcharges: AgentOverrides) -> list[str]:
    """Ce qu'une surcharge ÉLARGIRAIT : un projet ne fait que resserrer une version."""
    ecarts: list[str] = []
    for groupe in ("limits", "budget"):
        de_la_version = getattr(spec, groupe)
        du_projet = getattr(surcharges, groupe)
        for champ in type(de_la_version).model_fields:
            plafond, demande = getattr(de_la_version, champ), getattr(du_projet, champ)
            if demande is not None and plafond is not None and demande > plafond:
                ecarts.append(f"{groupe}.{champ} : {demande} dépasse les {plafond} de la version")
    if surcharges.tools is not None:
        en_trop = sorted(set(surcharges.tools) - _outils(spec))
        if en_trop:
            ecarts.append(f"outils que la version n'accorde pas : {', '.join(en_trop)}")
    return ecarts


def effective(spec: AgentSpec, surcharges: AgentOverrides) -> AgentSpec:
    """La version, surcharges appliquées : ce qui tournera."""
    resultat = spec.model_copy(deep=True)
    for groupe in ("limits", "budget"):
        cible = getattr(resultat, groupe)
        for champ in type(cible).model_fields:
            demande = getattr(getattr(surcharges, groupe), champ)
            if demande is not None:
                setattr(cible, champ, demande)
    if surcharges.tools is not None:
        gardes = set(surcharges.tools)
        for serveur in resultat.mcp_servers:
            serveur.tools = [outil for outil in serveur.tools if outil in gardes]
    return resultat


class AgentIndisponible(RuntimeError):  # noqa: N818 - un refus motivé, dit au run
    """L'agent qu'un acteur nomme n'existe pas, n'est pas actif, ou a expiré : le run ne part pas."""


@dataclass(frozen=True)
class AgentResolu:
    agent: Agent
    version: int
    spec: AgentSpec


async def resoudre_l_agent(
    session: AsyncSession, org_id: str, project_id: str, reference: str
) -> AgentResolu:
    """`slug@N` : cette version ; `slug` : l'épinglée du projet, sinon la dernière. Les surcharges
    du projet s'appliquent dans les deux cas : elles ne font que resserrer."""
    slug, _, demandee = reference.partition("@")
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None:
        raise AgentIndisponible(f"l'agent `{slug}` n'existe pas dans l'organisation du projet")
    if agent.status != "active":
        raise AgentIndisponible(f"l'agent `{slug}` est {agent.status} : le run ne part pas")
    if agent.expires_at is not None and _aware(agent.expires_at) <= utcnow():
        raise AgentIndisponible(f"l'agent `{slug}` a expiré le {agent.expires_at:%Y-%m-%d}")
    epingle = (
        await session.execute(
            select(ProjectAgent).where(
                ProjectAgent.project_id == project_id, ProjectAgent.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    if demandee:
        version = int(demandee)
    elif epingle is not None:
        version = epingle.version
    else:
        version = (
            await session.execute(
                select(AgentVersion.version)
                .where(AgentVersion.agent_id == agent.id)
                .order_by(AgentVersion.version.desc())
                .limit(1)
            )
        ).scalar_one()
    ligne = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if ligne is None:
        raise AgentIndisponible(f"l'agent `{slug}` n'a pas de version {version}")
    spec = AgentSpec.model_validate(ligne.spec)
    surcharges = (
        AgentOverrides.model_validate(epingle.overrides or {}) if epingle is not None else AgentOverrides()
    )
    return AgentResolu(agent=agent, version=version, spec=effective(spec, surcharges))


__all__ = [
    "AgentIndisponible",
    "AgentResolu",
    "effective",
    "elargissements",
    "erreurs_d_une_version",
    "resoudre_l_agent",
]
