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
from choregos_core import agent_catalogue as _catalogue
from choregos_core.agent_catalogue import Catalogue, CatalogueEntry, CatalogueSkill, entry_of
from choregos_core.compat import alias_names
from choregos_core.plugins.sources import SourceError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentVersion, OrgPlugin, OrgPluginFile, Skill, SkillVersion
from ..rbac import SYSTEM
from ..schemas import AgentSpec
from ..schemas.agents import MAX_TAGS
from .agent_releases import after_publish
from .agents import digest, version_errors
from .installation import install_skills_and_agents
from .network import check_agent_network
from .skill_gate import platform_columns
from .skills import empreinte as skill_digest

#: The prefix of the author of a version the catalogue wrote: `plugin:<plugin>/<slug>@<version>`.
PREFIX = "plugin:"
#: The author before S43-09, `catalogue:<slug>@<version>`; the migration `20261028_1200` rewrites it,
#: and an agent a replica still on the old code installs during a rolling upgrade is recognised too.
LEGACY = "catalogue:"
#: Suffix of the author of version 1 when the agent's text no longer follows the catalogue's (S39-25):
#: the upgrade proposes, it does not publish over what the organisation wrote.
DIVERGE = "!diverged"


@dataclass(frozen=True, slots=True)
class OrgState:
    installed: bool
    installed_version: int | None
    #: The agent of that name comes from the catalogue; otherwise it is the organisation's own, untouchable.
    catalogue_agent: bool
    update_available: bool
    #: The scope of the agent of that name (ADR 0061), if there is one.
    scope: str | None = None
    #: The agent comes from the catalogue but its latest version is not the catalogue's text and was
    #: marked as written by hand (backfill of S39-25): an upgrade is refused with that reason.
    divergent: bool = False


def author_of(entry: CatalogueEntry) -> str:
    return entry.provenance


def from_catalogue(created_by: str | None, entry: CatalogueEntry) -> bool:
    """The version was written by the catalogue for this entry's plugin (or before S43-09)."""
    author = created_by or ""
    return author.startswith((f"{PREFIX}{entry.plugin}/", LEGACY))


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
    ordered = tuple(sorted(entries, key=lambda e: (e.kind != "internal", e.slug)))
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


def spec_of(entry: CatalogueEntry) -> AgentSpec:
    return AgentSpec.model_validate(entry.document.get("spec") or {})


async def state(session: AsyncSession, org_id: Any, entry: CatalogueEntry) -> OrgState:
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == entry.slug))
    ).scalar_one_or_none()
    if agent is None:
        return OrgState(False, None, False, False)
    versions = list(
        (
            await session.execute(
                select(AgentVersion).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version)
            )
        ).scalars()
    )
    first, latest = versions[0], versions[-1]
    own = from_catalogue(first.created_by, entry)
    changed = latest.checksum != digest(spec_of(entry))
    diverged = own and (first.created_by or "").endswith(DIVERGE) and changed
    return OrgState(True, latest.version, own, own and changed, agent.scope, diverged)


def _skill(view: Catalogue, name: str, entry: CatalogueEntry) -> CatalogueSkill:
    found = view.skills.get(name)
    if found is None:
        from ..errors import unprocessable

        raise unprocessable(
            f"the catalogue entry `{entry.slug}` names the skill `{name}`, which no plugin ships"
        )
    return found


async def install(
    session: AsyncSession,
    org_id: Any,
    entry: CatalogueEntry,
    principal: Any,
    view: Catalogue | None = None,
) -> list[str]:
    """Its skills then the agent, as version 1; returns the names of what was born here. Each carries its
    plugin's provenance, as if the plugin had installed it."""
    view = view or await catalogue_of(session, org_id)
    installed: dict[str, list[str]] = {"skills": [], "agents": []}
    for name in entry.skills:
        skill = _skill(view, name, entry)
        done = await install_skills_and_agents(
            session, org_id, [skill.files], [], skill.provenance, what=f"the catalogue entry `{entry.slug}`"
        )
        installed["skills"] += done["skills"]
    done = await install_skills_and_agents(
        session, org_id, [], [entry.document], author_of(entry), what=f"the catalogue entry `{entry.slug}`"
    )
    installed["agents"] += done["agents"]
    if installed["agents"]:
        await record(session, principal, "agent.catalogue.install", org_id=org_id, target_type="agent",
                     target_id=entry.slug, version=entry.version, plugin=entry.plugin)  # fmt: skip
    return installed["skills"] + installed["agents"]


async def upgrade(
    session: AsyncSession,
    org_id: Any,
    entry: CatalogueEntry,
    principal: Any,
    view: Catalogue | None = None,
) -> int | None:
    """The agent's next version when the catalogue changed; its skills likewise. Returns the version
    published, or `None` when nothing changed. Project pins do not move."""
    view = view or await catalogue_of(session, org_id)
    for name in entry.skills:
        skill = _skill(view, name, entry)
        await _upgrade_skill(session, org_id, name, skill.files, skill.provenance)
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == entry.slug))
    ).scalar_one()
    spec = spec_of(entry)
    version_errors(spec)
    await check_agent_network(session, org_id, spec)
    latest = (
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
    added = [t for t in entry.tags if t not in (agent.tags or [])]
    agent.tags = [*(agent.tags or []), *added][:MAX_TAGS]
    if latest is not None and latest.checksum == digest(spec):
        return None
    version = (latest.version if latest else 0) + 1
    session.add(
        AgentVersion(
            agent_id=agent.id,
            org_id=org_id,
            version=version,
            spec=spec.model_dump(mode="json"),
            checksum=digest(spec),
            created_by=author_of(entry),
            message=f"Catalogue update to {entry.plugin}/{entry.slug}@{entry.version}",
        )
    )
    await session.flush()
    await record(session, principal, "agent.publish", org_id=org_id, target_type="agent", target_id=agent.id,
                 version=version, via=author_of(entry))  # fmt: skip
    # What an upgrade does to the channels is the agent's policy (ADR 0071).
    await after_publish(session, principal, agent, version, author_of(entry))
    return version


async def _upgrade_skill(
    session: AsyncSession, org_id: Any, name: str, files: dict[str, str], by: str
) -> None:
    skill = (
        await session.execute(select(Skill).where(Skill.org_id == org_id, Skill.slug == name))
    ).scalar_one_or_none()
    if skill is None:
        await install_skills_and_agents(
            session, org_id, [files], [], by, what=f"the catalogue skill `{name}`"
        )
        return
    latest = (
        await session.execute(select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill.id))
    ).scalar() or 0
    current = (
        await session.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == latest)
        )
    ).scalar_one_or_none()
    if current is not None and current.digest == skill_digest(files):
        return
    session.add(
        SkillVersion(
            skill_id=skill.id,
            org_id=org_id,
            version=latest + 1,
            files=dict(files),
            digest=skill_digest(files),
            created_by=by,
            **platform_columns(files),
        )
    )
    await session.flush()
    await record(session, SYSTEM, "skill.publish", org_id=org_id, target_type="skill", target_id=skill.id,
                 version=latest + 1, via=by)  # fmt: skip


def offer(entry: CatalogueEntry, settings: Any) -> tuple[bool, str | None]:
    """A client that calls from its publisher's cloud (claude.ai, ChatGPT) is offered only if the
    door accepts it: OAuth enabled, a client registered for it, an https address. The rule is the
    one of the console's clients page: nothing is listed that would not work."""
    if entry.reach != "cloud":
        return True, None
    missing = []
    if not settings.mcp_oauth_enabled:
        missing.append("the MCP door does not accept OAuth (`global.mcp.oauth`)")
    elif entry.client not in settings.mcp_oauth_clients:
        missing.append(f"no OAuth client is registered for `{entry.client}`")
    if not str(settings.public_url).startswith("https://"):
        missing.append("the console's public address is not https")
    return (not missing), ("; ".join(missing) or None)


def named_agents(workflow: Workflow) -> set[str]:
    """The registry agents the actors of a workflow name (`agent: slug[@version]`)."""
    return {
        str(actor.agent).split("@", 1)[0]
        for actor in workflow.actors.values()
        if isinstance(actor, AgentActor) and actor.agent
    }


async def install_named_agents(
    session: AsyncSession, org_id: Any, workflows: list[Workflow], principal: Any
) -> list[str]:
    """A workflow that names a catalogue agent the organisation lacks installs it — through the plugin
    that ships it, with that plugin's provenance — or the work item would die at its first run
    (`AgentIndisponible`). An agent already there (from the catalogue or the organisation's own) is not
    touched; a name neither the organisation nor any plugin knows is left to the validator and the run."""
    installed: list[str] = []
    slugs = sorted(set().union(*(named_agents(w) for w in workflows)) if workflows else set())
    view = await catalogue_of(session, org_id) if slugs else Catalogue()
    for slug in slugs:
        found = view.entry(slug)
        if found is None or found.kind != "internal":
            continue
        installed += await install(session, org_id, found, principal, view)
    return installed


def catalogue() -> Catalogue:
    # Through the function, not an imported name: a test that replaces the catalogue sees it here too.
    return offline_catalogue()


EtatDansLOrganisation = OrgState  # lang: fr-ok alias:internal
PREFIXE = PREFIX  # lang: fr-ok alias:internal
auteur = author_of  # lang: fr-ok alias:internal
spec_de = spec_of  # lang: fr-ok alias:internal
etat = state  # lang: fr-ok alias:internal
installer = install  # lang: fr-ok alias:internal
mettre_a_jour = upgrade  # lang: fr-ok alias:internal
offre = offer  # lang: fr-ok alias:internal
agents_nommes = named_agents  # lang: fr-ok alias:internal
installer_les_agents_nommes = install_named_agents  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "EtatDansLOrganisation": "OrgState",  # lang: fr-ok alias:internal
        "PREFIXE": "PREFIX",  # lang: fr-ok alias:internal
        "auteur": "author_of",  # lang: fr-ok alias:internal
        "spec_de": "spec_of",  # lang: fr-ok alias:internal
        "etat": "state",  # lang: fr-ok alias:internal
        "installer": "install",
        "mettre_a_jour": "upgrade",  # lang: fr-ok alias:internal
        "offre": "offer",  # lang: fr-ok alias:internal
        "agents_nommes": "named_agents",  # lang: fr-ok alias:internal
        "installer_les_agents_nommes": "install_named_agents",  # lang: fr-ok alias:internal
    },
)

__all__ = [
    "OrgState",
    "catalogue",
    "catalogue_instructions",
    "catalogue_of",
    "from_catalogue",
    "install",
    "install_named_agents",
    "named_agents",
    "offer",
    "offline_catalogue",
    "spec_of",
    "state",
    "upgrade",
]
