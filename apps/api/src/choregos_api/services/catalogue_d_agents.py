# SPDX-License-Identifier: Apache-2.0
"""Le catalogue d'agents, côté organisation (ADR 0040) : ce qu'il propose, ce qui en est installé, ce
qu'une mise à jour changerait — et l'installation des agents qu'un workflow nomme.

Installer n'écrase jamais : un agent du catalogue entre dans l'organisation en version 1 ; une mise à
jour publie la version SUIVANTE, et seulement si le texte du catalogue a changé. Un agent de
l'organisation qui porte le même nom sans venir du catalogue l'emporte : le catalogue ne le touche pas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from choregos_contracts import Workflow
from choregos_contracts.workflow import AgentActor
from choregos_core import catalogue_d_agents as _catalogue
from choregos_core.catalogue_d_agents import EntreeDuCatalogue, fichiers_de_la_skill
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentVersion, Skill, SkillVersion
from ..rbac import SYSTEM
from ..schemas import AgentSpec
from .agents import empreinte, erreurs_d_une_version
from .installation import installer_skills_et_agents
from .skills import empreinte as empreinte_de_skill

#: Préfixe de l'auteur d'une version venue du catalogue : `catalogue:<slug>@<version>`.
PREFIXE = "catalogue:"


@dataclass(frozen=True, slots=True)
class EtatDansLOrganisation:
    installe: bool
    version_installee: int | None
    #: L'agent du même nom vient du catalogue ; sinon, c'est celui de l'organisation, intouchable.
    du_catalogue: bool
    mise_a_jour: bool


def auteur(entree_: EntreeDuCatalogue) -> str:
    return f"{PREFIXE}{entree_.slug}@{entree_.version}"


def spec_de(entree_: EntreeDuCatalogue) -> AgentSpec:
    return AgentSpec.model_validate(entree_.document.get("spec") or {})


async def etat(session: AsyncSession, org_id: Any, entree_: EntreeDuCatalogue) -> EtatDansLOrganisation:
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == entree_.slug))
    ).scalar_one_or_none()
    if agent is None:
        return EtatDansLOrganisation(False, None, False, False)
    versions = list(
        (
            await session.execute(
                select(AgentVersion).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version)
            )
        ).scalars()
    )
    premiere, derniere = versions[0], versions[-1]
    du_catalogue = (premiere.created_by or "").startswith(PREFIXE)
    change = derniere.checksum != empreinte(spec_de(entree_))
    return EtatDansLOrganisation(True, derniere.version, du_catalogue, du_catalogue and change)


async def installer(
    session: AsyncSession, org_id: Any, entree_: EntreeDuCatalogue, principal: Any
) -> list[str]:
    """Ses skills puis l'agent, en version 1 ; rend les noms de ce qui est né ici."""
    skills = [fichiers_de_la_skill(nom) for nom in entree_.skills]
    installes = await installer_skills_et_agents(
        session,
        org_id,
        skills,
        [entree_.document],
        auteur(entree_),
        quoi=f"the catalogue entry `{entree_.slug}`",
    )
    if installes["agents"]:
        await record(session, principal, "agent.catalogue.install", org_id=org_id, target_type="agent",
                     target_id=entree_.slug, version=entree_.version)  # fmt: skip
    return installes["skills"] + installes["agents"]


async def mettre_a_jour(
    session: AsyncSession, org_id: Any, entree_: EntreeDuCatalogue, principal: Any
) -> int | None:
    """La version suivante de l'agent si le catalogue a changé ; ses skills de même. Rend la version
    publiée, ou `None` quand rien n'a changé. Les épingles des projets ne bougent pas."""
    for nom in entree_.skills:
        await _mettre_a_jour_la_skill(session, org_id, nom, auteur(entree_))
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == entree_.slug))
    ).scalar_one()
    spec = spec_de(entree_)
    erreurs_d_une_version(spec)
    derniere = (
        (
            await session.execute(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent.id)
                .order_by(AgentVersion.version.desc())
            )
        )
        .scalars()
        .first()
    )
    if derniere is not None and derniere.checksum == empreinte(spec):
        return None
    version = (derniere.version if derniere else 0) + 1
    session.add(
        AgentVersion(
            agent_id=agent.id,
            org_id=org_id,
            version=version,
            spec=spec.model_dump(mode="json"),
            checksum=empreinte(spec),
            created_by=auteur(entree_),
        )
    )
    await session.flush()
    await record(session, principal, "agent.publish", org_id=org_id, target_type="agent", target_id=agent.id,
                 version=version, via=auteur(entree_))  # fmt: skip
    return version


async def _mettre_a_jour_la_skill(session: AsyncSession, org_id: Any, nom: str, qui: str) -> None:
    fichiers = fichiers_de_la_skill(nom)
    skill = (
        await session.execute(select(Skill).where(Skill.org_id == org_id, Skill.slug == nom))
    ).scalar_one_or_none()
    if skill is None:
        await installer_skills_et_agents(
            session, org_id, [fichiers], [], qui, quoi=f"the catalogue skill `{nom}`"
        )
        return
    derniere = (
        await session.execute(select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill.id))
    ).scalar() or 0
    actuelle = (
        await session.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == derniere)
        )
    ).scalar_one_or_none()
    if actuelle is not None and actuelle.digest == empreinte_de_skill(fichiers):
        return
    session.add(
        SkillVersion(
            skill_id=skill.id,
            org_id=org_id,
            version=derniere + 1,
            files=dict(fichiers),
            digest=empreinte_de_skill(fichiers),
            created_by=qui,
        )
    )
    await session.flush()
    await record(session, SYSTEM, "skill.publish", org_id=org_id, target_type="skill", target_id=skill.id,
                 version=derniere + 1, via=qui)  # fmt: skip


def offre(entree_: EntreeDuCatalogue, settings: Any) -> tuple[bool, str | None]:
    """Un client qui appelle depuis le cloud de son éditeur (claude.ai, ChatGPT) n'est proposé que si
    la porte l'accepte : OAuth activé, un client enregistré pour lui, une adresse en https. La règle
    est celle de la page des clients (`etatDuClient`) : rien n'est listé qui ne marcherait pas."""
    if entree_.portee != "cloud":
        return True, None
    manque = []
    if not settings.mcp_oauth_enabled:
        manque.append("the MCP door does not accept OAuth (`global.mcp.oauth`)")
    elif entree_.client not in settings.mcp_oauth_clients:
        manque.append(f"no OAuth client is registered for `{entree_.client}`")
    if not str(settings.public_url).startswith("https://"):
        manque.append("the console's public address is not https")
    return (not manque), ("; ".join(manque) or None)


def agents_nommes(workflow: Workflow) -> set[str]:
    """Les agents du registre que les acteurs d'un workflow nomment (`agent: slug[@version]`)."""
    return {
        str(acteur.agent).split("@", 1)[0]
        for acteur in workflow.actors.values()
        if isinstance(acteur, AgentActor) and acteur.agent
    }


async def installer_les_agents_nommes(
    session: AsyncSession, org_id: Any, workflows: list[Workflow], principal: Any
) -> list[str]:
    """Un workflow qui nomme un agent du catalogue absent de l'organisation l'installe : sinon le
    ticket mourrait au premier run (`AgentIndisponible`). Un agent déjà là — du catalogue ou de
    l'organisation — n'est pas touché ; un nom que ni l'organisation ni le catalogue ne connaît reste
    au validateur et au run."""
    installes: list[str] = []
    for slug in sorted(set().union(*(agents_nommes(w) for w in workflows)) if workflows else set()):
        trouve = _catalogue.entree(slug)
        if trouve is None or trouve.genre != "internal":
            continue
        installes += await installer(session, org_id, trouve, principal)
    return installes


def catalogue() -> tuple[EntreeDuCatalogue, ...]:
    # Par le module, pas par un nom importé : un test qui remplace le catalogue le voit ici aussi.
    return _catalogue.entrees()


__all__ = [
    "EtatDansLOrganisation",
    "agents_nommes",
    "catalogue",
    "etat",
    "installer",
    "installer_les_agents_nommes",
    "mettre_a_jour",
    "offre",
]
