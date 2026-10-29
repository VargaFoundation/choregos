# SPDX-License-Identifier: Apache-2.0
"""The agent catalogue, seen from an organisation (ADR 0040): what it offers, what of it is installed,
what an upgrade would change — and the installation of the agents a workflow names.

Since S43-09 the catalogue is a view over plugins (ADR 0093 §6): the agents of the deployment's
offline plugin directory, replaced, plugin by plugin, by those of the plugins the organisation installed
(`catalogue_of`). An entry installed from here carries its plugin's provenance,
`plugin:<plugin>/<slug>@<catalogue version>`, so the plugin's install and upgrade recognise it.

Installing never overwrites: a catalogue agent enters the organisation as version 1; an upgrade publishes
the NEXT version, and only when the catalogue's text changed. An agent of the organisation that bears
the same name without coming from the catalogue wins: the catalogue does not touch it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from choregos_contracts import Workflow
from choregos_contracts.workflow import AgentActor
from choregos_core import catalogue_d_agents as _catalogue
from choregos_core.catalogue_d_agents import Catalogue, CatalogueSkill, EntreeDuCatalogue, entry_of
from choregos_core.plugins.sources import SourceError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentVersion, OrgPlugin, OrgPluginFile, Skill, SkillVersion
from ..rbac import SYSTEM
from ..schemas import AgentSpec
from ..schemas.agents import MAX_TAGS
from .agent_releases import after_publish
from .agents import empreinte, erreurs_d_une_version
from .installation import installer_skills_et_agents
from .network import check_agent_network
from .skill_gate import platform_columns
from .skills import empreinte as empreinte_de_skill

#: The prefix of the author of a version the catalogue wrote: `plugin:<plugin>/<slug>@<version>`.
PREFIXE = "plugin:"
#: The author before S43-09, `catalogue:<slug>@<version>`; the migration `20261028_1200` rewrites it,
#: and an agent a replica still on the old code installs during a rolling upgrade is recognised too.
LEGACY = "catalogue:"
#: Suffix of the author of version 1 when the agent's text no longer follows the catalogue's (S39-25):
#: the upgrade proposes, it does not publish over what the organisation wrote.
DIVERGE = "!diverged"


@dataclass(frozen=True, slots=True)
class EtatDansLOrganisation:
    installe: bool
    version_installee: int | None
    #: L'agent du même nom vient du catalogue ; sinon, c'est celui de l'organisation, intouchable.
    du_catalogue: bool
    mise_a_jour: bool
    #: The scope of the agent of that name (ADR 0061), if there is one.
    scope: str | None = None
    #: The agent comes from the catalogue but its latest version is not the catalogue's text and was
    #: marked as written by hand (backfill of S39-25): an upgrade is refused with that reason.
    divergent: bool = False


def auteur(entree_: EntreeDuCatalogue) -> str:
    return entree_.provenance


def from_catalogue(created_by: str | None, entree_: EntreeDuCatalogue) -> bool:
    """The version was written by the catalogue for this entry's plugin (or before S43-09)."""
    author = created_by or ""
    return author.startswith((f"{PREFIXE}{entree_.plugin}/", LEGACY))


# -- the view ---------------------------------------------------------------------------------------------


def offline_catalogue() -> Catalogue:
    """The deployment's offline directory (`global.plugins.offline`, `CHOREGOS_PLUGINS_OFFLINE`), else the
    core's default one (this repository's `plugins/offline/`, the image's `/opt/choregos/plugins`)."""
    from .plugin_sources import plugins_config

    try:
        directory = plugins_config().offline
    except SourceError:
        directory = None
    return _catalogue.catalogue(directory)


def _installed_catalogue(row: OrgPlugin, files: dict[str, bytes]) -> Catalogue | None:
    """What an installed plugin offers, from the files the installer kept (`org_plugin_files`); `None` for
    a row installed before S43-09, which kept none: the offline directory answers for it."""
    contents = dict((row.manifest or {}).get("contents") or {})
    agents = [ref for ref in contents.get("agents") or [] if ref in files]
    if not agents:
        return None
    entries = [entry_of(yaml.safe_load(files[ref].decode("utf-8")), row.name, row.version) for ref in agents]
    skills: dict[str, CatalogueSkill] = {}
    for ref in contents.get("skills") or []:
        prefix = f"{ref}/"
        texts = {p.removeprefix(prefix): c.decode("utf-8") for p, c in files.items() if p.startswith(prefix)}
        if texts:
            skills[Path(ref).name] = CatalogueSkill(Path(ref).name, row.name, row.version, texts)
    ordered = tuple(sorted(entries, key=lambda e: (e.genre != "internal", e.slug)))
    return Catalogue(ordered, skills, (), {row.name: row.version})


async def catalogue_of(session: AsyncSession, org_id: Any) -> Catalogue:
    """The offline directory's plugins, each replaced by the version the organisation installed."""
    view = Catalogue()
    query = select(OrgPlugin).where(OrgPlugin.org_id == org_id).order_by(OrgPlugin.name)
    rows = list((await session.execute(query)).scalars())
    for row in rows:
        stored = (
            await session.execute(
                select(OrgPluginFile.path, OrgPluginFile.content).where(
                    OrgPluginFile.org_id == org_id, OrgPluginFile.digest == row.digest
                )
            )
        ).all()
        installed = _installed_catalogue(row, dict(stored))
        if installed is not None:
            view = installed.over(view)
    return view.over(catalogue())


async def catalogue_instructions(session: AsyncSession, org_id: Any, slug: str) -> str | None:
    """The instructions the catalogue offers this organisation for `slug`; `None` when no plugin does."""
    found = (await catalogue_of(session, org_id)).entry(slug)
    return None if found is None else str(found.document["spec"].get("instructions", ""))


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
    du_catalogue = from_catalogue(premiere.created_by, entree_)
    change = derniere.checksum != empreinte(spec_de(entree_))
    divergent = du_catalogue and (premiere.created_by or "").endswith(DIVERGE) and change
    return EtatDansLOrganisation(
        True, derniere.version, du_catalogue, du_catalogue and change, agent.scope, divergent
    )


def _skill(view: Catalogue, nom: str, entree_: EntreeDuCatalogue) -> CatalogueSkill:
    found = view.skills.get(nom)
    if found is None:
        from ..errors import unprocessable

        raise unprocessable(
            f"the catalogue entry `{entree_.slug}` names the skill `{nom}`, which no plugin ships"
        )
    return found


async def installer(
    session: AsyncSession,
    org_id: Any,
    entree_: EntreeDuCatalogue,
    principal: Any,
    view: Catalogue | None = None,
) -> list[str]:
    """Its skills then the agent, as version 1; returns the names of what was born here. Each carries its
    plugin's provenance, as if the plugin had installed it."""
    view = view or await catalogue_of(session, org_id)
    installes: dict[str, list[str]] = {"skills": [], "agents": []}
    for nom in entree_.skills:
        skill = _skill(view, nom, entree_)
        done = await installer_skills_et_agents(
            session, org_id, [skill.files], [], skill.provenance, quoi=f"the catalogue entry `{entree_.slug}`"
        )
        installes["skills"] += done["skills"]
    done = await installer_skills_et_agents(
        session, org_id, [], [entree_.document], auteur(entree_), quoi=f"the catalogue entry `{entree_.slug}`"
    )
    installes["agents"] += done["agents"]
    if installes["agents"]:
        await record(session, principal, "agent.catalogue.install", org_id=org_id, target_type="agent",
                     target_id=entree_.slug, version=entree_.version, plugin=entree_.plugin)  # fmt: skip
    return installes["skills"] + installes["agents"]


async def mettre_a_jour(
    session: AsyncSession,
    org_id: Any,
    entree_: EntreeDuCatalogue,
    principal: Any,
    view: Catalogue | None = None,
) -> int | None:
    """The agent's next version when the catalogue changed; its skills likewise. Returns the version
    published, or `None` when nothing changed. Project pins do not move."""
    view = view or await catalogue_of(session, org_id)
    for nom in entree_.skills:
        skill = _skill(view, nom, entree_)
        await _mettre_a_jour_la_skill(session, org_id, nom, skill.files, skill.provenance)
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == entree_.slug))
    ).scalar_one()
    spec = spec_de(entree_)
    erreurs_d_une_version(spec)
    await check_agent_network(session, org_id, spec)
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
    # A tag-only change of the catalogue still reaches an upgraded agent (S40-10): the entry's tags are
    # added, an organisation's own are never removed, and the cap holds (its own come first).
    nouveaux = [t for t in entree_.tags if t not in (agent.tags or [])]
    agent.tags = [*(agent.tags or []), *nouveaux][:MAX_TAGS]
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
            message=f"Catalogue update to {entree_.plugin}/{entree_.slug}@{entree_.version}",
        )
    )
    await session.flush()
    await record(session, principal, "agent.publish", org_id=org_id, target_type="agent", target_id=agent.id,
                 version=version, via=auteur(entree_))  # fmt: skip
    # What an upgrade does to the channels is the agent's policy (ADR 0071).
    await after_publish(session, principal, agent, version, auteur(entree_))
    return version


async def _mettre_a_jour_la_skill(
    session: AsyncSession, org_id: Any, nom: str, fichiers: dict[str, str], qui: str
) -> None:
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
            **platform_columns(fichiers),
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
    """A workflow that names a catalogue agent the organisation lacks installs it — through the plugin
    that ships it, with that plugin's provenance — or the work item would die at its first run
    (`AgentIndisponible`). An agent already there (from the catalogue or the organisation's own) is not
    touched; a name neither the organisation nor any plugin knows is left to the validator and the run."""
    installes: list[str] = []
    slugs = sorted(set().union(*(agents_nommes(w) for w in workflows)) if workflows else set())
    view = await catalogue_of(session, org_id) if slugs else Catalogue()
    for slug in slugs:
        trouve = view.entry(slug)
        if trouve is None or trouve.genre != "internal":
            continue
        installes += await installer(session, org_id, trouve, principal, view)
    return installes


def catalogue() -> Catalogue:
    # Through the function, not an imported name: a test that replaces the catalogue sees it here too.
    return offline_catalogue()


__all__ = [
    "EtatDansLOrganisation",
    "agents_nommes",
    "catalogue",
    "catalogue_instructions",
    "catalogue_of",
    "etat",
    "from_catalogue",
    "installer",
    "installer_les_agents_nommes",
    "mettre_a_jour",
    "offline_catalogue",
    "offre",
]
