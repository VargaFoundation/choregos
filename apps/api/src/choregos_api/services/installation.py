# SPDX-License-Identifier: Apache-2.0
"""What a template INSTALLS into the organisation of a project born of it (S20-07): its skills, then
its agents, as version 1 when they are absent and NEVER rewritten when they are there.

The organisation may have evolved them since the first project: one more project does not undo that
work. An agent names its skills by name, so they are installed first.

Then what extensions install into the PROJECT (S20-09): each extension goes to the installer declared
under its name; without one, the project is born without it, and the audit says so.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentVersion, Project, Skill, SkillVersion
from ..errors import unprocessable
from ..logging import get_logger
from ..rbac import SYSTEM
from ..schemas.agents import AgentCreate
from .agent_releases import after_publish
from .agents import digest as agent_digest
from .agents import require_complete, version_errors
from .hosted_images import pin_hosted_runtime
from .network import check_agent_network
from .skill_gate import platform_columns
from .skills import SkillRefused, digest, refuse_reserved_slug, validate

logger = get_logger("choregos.templates")


async def install_what_template_delivers(
    session: AsyncSession, project: Project, delivered: Any, template: str
) -> dict[str, list[str]]:
    """Returns what was installed: the names of the skills and agents born here, of the extensions
    installed and of those that no extension installs."""
    author = f"template:{template}"
    installed = await install_skills_and_agents(
        session,
        project.org_id,
        delivered.skills,
        delivered.agents,
        author,
        what=f"the template `{template}`",
        catalogue_provenance=getattr(delivered, "catalogue_agents", None),
    )
    installed |= {"extensions": [], "without_extension": []}
    await _install_extensions(session, project, delivered, author, installed)
    if delivered.knowledge:
        # The bases its agents read (S51-06, ADR 0117): created for the project, or reused.
        from .template_knowledge import install_template_knowledge

        done = await install_template_knowledge(
            session, project, delivered.knowledge, delivered.inputs, author
        )
        installed["knowledge"] = done["created"] + done["completed"] + done["reused"] + done["served"]
        installed["knowledge_skipped"] = done["skipped"]
    return installed


def _install_message(author: str) -> str:
    """The message of the version 1 the platform writes when it installs an agent (S40-12)."""
    source, _, name = author.partition(":")
    if source == "catalogue":
        return f"Installed from the catalogue ({name})"
    if source == "template":
        return f"Installed from the template {name}"
    if source == "plugin":
        return f"Installed from the plugin {name.partition('/')[0]} ({name.partition('/')[2]})"
    return f"Installed by {author}"


async def install_skills_and_agents(
    session: AsyncSession,
    org_id: Any,
    skills: list[dict[str, str]],
    agents: list[dict[str, Any]],
    author: str,
    *,
    what: str,
    catalogue_provenance: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Skills then agents into an organisation, as version 1 when absent and NEVER rewritten when present
    — the path shared by a template (S20-07) and the catalogue (ADR 0040). An agent names its skills, so
    they are installed first.

    `catalogue_provenance`: slug -> `catalogue:<slug>@<version>` for the agents a template takes from the
    catalogue (`catalogue:<slug>`). They carry the SAME provenance as a direct install (author of version
    1, scope `platform`) so the catalogue's upgrade recognises them (S39-25); the template stays in the
    audit trail (`via`).
    """
    installed: dict[str, list[str]] = {"skills": [], "agents": []}
    heads = []
    for files in skills:
        try:
            head = validate(files)
        except SkillRefused as refusal:
            raise unprocessable(f"{what} delivers a refused skill: {refusal}") from refusal
        # A new skill under a console page's slug could never be opened (S55-18): the install is refused
        # before any of its skills is written, as a person's own version is (S55-17).
        await refuse_reserved_slug(session, org_id, head.name, what=what)
        heads.append(head)
    for files, head in zip(skills, heads, strict=True):
        if (
            await session.execute(select(Skill.id).where(Skill.org_id == org_id, Skill.slug == head.name))
        ).first():
            continue
        skill = Skill(org_id=org_id, slug=head.name, description=head.description, status="active")
        session.add(skill)
        await session.flush()
        session.add(
            SkillVersion(
                skill_id=skill.id,
                org_id=org_id,
                version=1,
                files=dict(files),
                digest=digest(files),
                created_by=author,
                **platform_columns(files),
            )
        )
        await record(session, SYSTEM, "skill.publish", org_id=org_id, target_type="skill",
                     target_id=skill.id, version=1, via=author)  # fmt: skip
        installed["skills"].append(head.name)
    for document in agents:
        body = AgentCreate.model_validate(document)
        version_errors(body.spec)
        await require_complete(session, org_id, body.kind, body.spec)
        await check_agent_network(session, org_id, body.spec)
        if (
            await session.execute(select(Agent.id).where(Agent.org_id == org_id, Agent.slug == body.slug))
        ).first():
            continue
        body.spec = await pin_hosted_runtime(session, org_id, body.spec)
        provenance = (catalogue_provenance or {}).get(body.slug, author)
        agent = Agent(
            org_id=org_id,
            slug=body.slug,
            kind=body.kind,
            display_name=body.display_name,
            description=body.description,
            tags=list(body.tags),
            owner_id=None,
            status="active",
            # What the built-in catalogue or a plugin installs is common to everyone (ADR 0061, ADR
            # 0093); a template's agents belong to the organisation.
            scope="platform" if provenance.startswith(("catalogue:", "plugin:")) else "org",
        )
        session.add(agent)
        await session.flush()
        session.add(
            AgentVersion(
                agent_id=agent.id,
                org_id=org_id,
                version=1,
                spec=body.spec.model_dump(mode="json"),
                checksum=agent_digest(body.spec),
                created_by=provenance,
                message=_install_message(provenance),
            )
        )
        await session.flush()
        await after_publish(session, None, agent, 1, author)
        await record(session, SYSTEM, "agent.create", org_id=org_id, target_type="agent",
                     target_id=agent.id, via=author)  # fmt: skip
        installed["agents"].append(body.slug)
    await session.flush()
    return installed


async def _install_extensions(
    session: AsyncSession, project: Project, delivered: Any, author: str, installed: dict[str, list[str]]
) -> None:
    from ..extensions import template_installers

    installers = template_installers()
    for name, files in sorted(delivered.extensions.items()):
        installer = installers.get(name)
        if installer is None:
            logger.warning(
                "template.extension_without_installer", project=project.id, extension=name, via=author
            )
            await record(session, SYSTEM, "template.extension.skip", org_id=project.org_id,
                         target_type="project", target_id=project.id, extension=name, via=author,
                         reason="no active extension installs it")  # fmt: skip
            installed["without_extension"].append(name)
            continue
        result = await installer(session, project, dict(files), author)
        await record(session, SYSTEM, "template.extension.install", org_id=project.org_id,
                     target_type="project", target_id=project.id, extension=name, via=author,
                     result=result)  # fmt: skip
        installed["extensions"].append(name)
    await session.flush()
