# SPDX-License-Identifier: Apache-2.0
"""Ce qu'un gabarit INSTALLE dans l'organisation d'un projet qui naît de lui (S20-07) : ses skills,
puis ses agents — en version 1 quand ils n'y sont pas, JAMAIS réécrits quand ils y sont.

L'organisation a pu les faire évoluer depuis le premier projet : un projet de plus ne défait pas ce
travail. Un agent nomme ses skills par leur nom ; elles s'installent donc d'abord.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentVersion, Project, Skill, SkillVersion
from ..errors import unprocessable
from ..rbac import SYSTEM
from ..schemas.agents import AgentCreate
from .agents import empreinte as empreinte_de_l_agent
from .agents import erreurs_d_une_version
from .skills import SkillRefusee, empreinte, valider


async def installer_ce_que_livre_le_gabarit(
    session: AsyncSession, projet: Project, livree: Any, gabarit: str
) -> dict[str, list[str]]:
    """Rend ce qui a été installé : les noms des skills et des agents nés ici."""
    installes: dict[str, list[str]] = {"skills": [], "agents": []}
    auteur = f"template:{gabarit}"
    for fichiers in livree.skills:
        try:
            tete = valider(fichiers)
        except SkillRefusee as refus:
            raise unprocessable(f"le gabarit `{gabarit}` livre une skill refusée : {refus}") from refus
        if (
            await session.execute(
                select(Skill.id).where(Skill.org_id == projet.org_id, Skill.slug == tete.name)
            )
        ).first():
            continue
        skill = Skill(org_id=projet.org_id, slug=tete.name, description=tete.description, status="active")
        session.add(skill)
        await session.flush()
        session.add(
            SkillVersion(
                skill_id=skill.id,
                org_id=projet.org_id,
                version=1,
                files=dict(fichiers),
                digest=empreinte(fichiers),
                created_by=auteur,
            )
        )
        await record(session, SYSTEM, "skill.publish", org_id=projet.org_id, target_type="skill",
                     target_id=skill.id, version=1, via=auteur)  # fmt: skip
        installes["skills"].append(tete.name)
    for document in livree.agents:
        corps = AgentCreate.model_validate(document)
        erreurs_d_une_version(corps.spec)
        if (
            await session.execute(
                select(Agent.id).where(Agent.org_id == projet.org_id, Agent.slug == corps.slug)
            )
        ).first():
            continue
        agent = Agent(
            org_id=projet.org_id,
            slug=corps.slug,
            kind=corps.kind,
            display_name=corps.display_name,
            description=corps.description,
            owner_id=None,
            status="active",
        )
        session.add(agent)
        await session.flush()
        session.add(
            AgentVersion(
                agent_id=agent.id,
                org_id=projet.org_id,
                version=1,
                spec=corps.spec.model_dump(mode="json"),
                checksum=empreinte_de_l_agent(corps.spec),
                created_by=auteur,
            )
        )
        await record(session, SYSTEM, "agent.create", org_id=projet.org_id, target_type="agent",
                     target_id=agent.id, via=auteur)  # fmt: skip
        installes["agents"].append(corps.slug)
    await session.flush()
    return installes
