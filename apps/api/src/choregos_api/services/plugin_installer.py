# SPDX-License-Identifier: Apache-2.0
"""Installs a loaded plugin into an organisation, upgrades it, uninstalls it (ADR 0093, S43-04).

The plugin arrives already loaded and validated by `choregos_core.plugins` (S43-02), from a
`PluginSupply` (`plugin_supply.py`). The installer:

- validates everything once more against THIS organisation before writing anything: every agent through
  the full `AgentCreate` model, its instructions rendered, its network checked (`check_agent_network`);
  every skill through the skill library's validator; every eval suite (`plugin-evals.schema.json`)
  played on its agent, level 1 and its deterministic `cases`; procedure ids that no other procedure of
  the deployment or plugin of the organisation holds; the plugins it requires installed;
- installs skills then agents through the path the catalogue and templates share
  (`install_skills_and_agents`): version 1 when absent, never rewritten when present. Provenance is
  `plugin:<plugin>/<slug>@<catalogue version>` for an agent and `plugin:<plugin>/<skill>@<plugin version>`
  for a skill. An object of that name the organisation already has, and that the plugin did not
  install, wins and is left untouched (ADR 0040 §4);
- keeps the files the platform serves again (templates with their files, eval suites, procedures, since
  S43-09 its agents and skills, which the organisation's agent catalogue reads, and since #1372 its matrix
  cases, which the live eval matrix reads and nothing plays at install) in
  `org_plugin_files`, under the plugin's content digest and the organisation's RLS; an external client's
  `catalogue.client` and `catalogue.reach` are kept in the row's `details.clients` too;
- runs the plugin's servers and governs them as organisation connectors (`plugin_servers.py`, ADR 0096):
  each server is checked with the rest (image rules, a free connector name) and provisioned with it.

An upgrade follows ADR 0040: an agent the organisation changed (a version not written by this plugin)
is OFFERED the update, never overwritten; the others get the next version only when their text
changed; project pins do not move. Uninstalling removes what the plugin installed and nobody changed
or pins; it keeps the rest, which loses its plugin provenance (its scope becomes `org`), and says so in
the audit trail.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from choregos_core import procedures as core_procedures
from choregos_core.agent_catalogue import CatalogueEntry, entry_of
from choregos_core.agent_catalogue.evals import evaluate
from choregos_core.paths import PathOutsideError, SymlinkRefusedError, files_under, inside
from choregos_core.plugins import Plugin
from choregos_core.plugins.assistants import named_files as assistant_named_files
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import Settings, get_settings
from ..db.base import utcnow
from ..db.models import (
    Agent,
    AgentVersion,
    AssistantInstallation,
    Organization,
    OrgPlugin,
    OrgPluginFile,
    Project,
    ProjectAgent,
    Skill,
    SkillVersion,
)
from ..errors import conflict, not_found, unprocessable
from ..rbac import SYSTEM, Principal
from ..schemas.agents import MAX_TAGS, AgentCreate, AgentEvalCase
from ..schemas.plugins import InstalledPlugin, PluginContents
from . import plugin_servers
from .agent_catalogue import DIVERGE
from .agent_evals import evaluate as evaluate_cases
from .agent_releases import after_publish
from .agents import digest, require_complete, version_errors
from .installation import install_skills_and_agents
from .network import check_agent_network
from .plugin_files import (
    _materialise,
    _row,
    installed_row,
    plugin_assistant,
    plugin_text,
)
from .plugin_supply import PluginSupply, semver_key
from .skill_gate import platform_columns
from .skills import SkillRefused, refuse_reserved_slug, validate
from .skills import digest as skill_digest

PREFIX = "plugin:"
INSTALLED = "installed"
SERVERS_PENDING = "servers_pending"


def provenance(plugin: str, slug: str, version: str | int) -> str:
    """`plugin:<plugin>/<slug>@<version>`: the author of what a plugin writes (ADR 0093 §6)."""
    return f"{PREFIX}{plugin}/{slug}@{version}"


def _ours(created_by: str | None, plugin: str) -> bool:
    return (created_by or "").startswith(f"{PREFIX}{plugin}/")


# -- what a plugin brings, read and validated -----------------------------------------------------------


@dataclass
class _Prepared:
    plugin: Plugin
    agents: dict[str, CatalogueEntry] = field(default_factory=dict)
    specs: dict[str, AgentCreate] = field(default_factory=dict)
    skills: dict[str, dict[str, str]] = field(default_factory=dict)
    templates: dict[str, str] = field(default_factory=dict)
    evals: list[str] = field(default_factory=list)
    procedures: dict[str, str] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.plugin.name


def _contents(plugin: Plugin) -> dict[str, list[str]]:
    return dict(plugin.manifest.get("contents") or {})


def _dir_files(root: Path, ref: str) -> dict[str, Path]:
    try:
        return files_under(inside(root, ref))
    except (PathOutsideError, SymlinkRefusedError) as error:
        raise unprocessable(f"`{ref}`: {error}") from error


def _text(path: Path, ref: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise unprocessable(f"`{ref}`: not UTF-8 text") from error


def _agent_errors(error: ValidationError) -> list[dict[str, Any]]:
    return [{"loc": list(e["loc"]), "msg": e["msg"]} for e in error.errors()]


async def _prepare(
    session: AsyncSession, org_id: str, plugin: Plugin, settings: Settings, *, replacing: OrgPlugin | None
) -> _Prepared:
    """Everything checked against this organisation, nothing written: a refusal leaves no trace."""
    prepared = _Prepared(plugin)
    root, contents = plugin.root, _contents(plugin)

    for required in (plugin.manifest.get("requires") or {}).get("plugins") or []:
        name = str(required.get("name") if isinstance(required, dict) else required)
        if await _row(session, org_id, name) is None:
            raise unprocessable(f"`{plugin.name}` requires the plugin `{name}`, which is not installed")

    for ref in contents.get("skills", []):
        paths = _dir_files(root, ref)
        files = {name: _text(path, f"{ref}/{name}") for name, path in paths.items()}
        try:
            head = validate(files)
        except SkillRefused as refusal:
            raise unprocessable(f"`{ref}`: the skill is refused: {refusal}") from refusal
        # Before a second approval is asked and before anything is written (S55-18).
        await refuse_reserved_slug(session, org_id, head.name, what=f"the plugin `{plugin.name}`")
        prepared.skills[head.name] = files
        for name, path in paths.items():
            prepared.files[f"{ref}/{name}"] = path.read_bytes()

    for ref in [*contents.get("agents", []), *contents.get("matrix_cases", [])]:
        prepared.files[ref] = inside(root, ref).read_bytes()

    # An assistant's file and the files it names ship with the plugin: an installation reads them again.
    named_files: list[str] = []
    named_directories: list[str] = []
    for document in plugin.assistants.values():
        refs, directories = assistant_named_files(document)
        named_files.extend(refs)
        named_directories.extend(directories)
    for ref in [*contents.get("assistants", []), *named_files]:
        prepared.files[ref] = inside(root, ref).read_bytes()
    for ref in named_directories:
        for name, path in _dir_files(root, ref).items():
            prepared.files[f"{ref}/{name}"] = path.read_bytes()

    for slug, document in sorted(plugin.agents.items()):
        entry = entry_of(document, plugin.name, plugin.version)
        try:
            body = AgentCreate.model_validate(entry.document)
        except ValidationError as error:
            raise unprocessable(
                f"the agent `{slug}` of `{plugin.name}` is not a valid agent", _agent_errors(error)
            ) from error
        version_errors(body.spec)
        await require_complete(session, org_id, body.kind, body.spec)
        await check_agent_network(session, org_id, body.spec)
        prepared.agents[slug], prepared.specs[slug] = entry, body

    _evals(prepared, contents.get("evals", []))

    for ref in contents.get("templates", []):
        paths = _dir_files(root, ref)
        manifest = yaml.safe_load(_text(paths["manifest.yaml"], f"{ref}/manifest.yaml")) or {}
        prepared.templates[str(manifest["metadata"]["name"])] = ref
        for name, path in paths.items():
            prepared.files[f"{ref}/{name}"] = path.read_bytes()

    await _procedures(session, org_id, prepared, settings, replacing)
    await plugin_servers.check(
        session, org_id, plugin, settings, replacing=replacing.name if replacing is not None else None
    )
    return prepared


def _evals(prepared: _Prepared, refs: list[str]) -> None:
    """Each eval suite (`plugin-evals.schema.json`, checked by the loader) played on its agent: level 1
    on the rendered instructions, then every deterministic case of `cases` on its sample input, as the
    API's publish gate plays them (`agent_evals`). A `live` case needs a model: it is not run here. A
    plugin whose own evals fail is refused."""
    root = prepared.plugin.root
    for ref in refs:
        path = inside(root, ref)
        suite = yaml.safe_load(_text(path, ref))
        slug = str(suite["agent"])
        result = evaluate(suite, agent=prepared.agents[slug])
        if not result.passed:
            raise unprocessable(
                f"`{ref}`: the eval suite of `{result.agent}` fails on the plugin's own instructions",
                [{"loc": [ref], "msg": f"missing {result.missing}, forbidden {result.forbidden}"}],
            )
        try:
            cases = [AgentEvalCase.model_validate(case) for case in suite.get("cases") or []]
        except ValidationError as error:
            raise unprocessable(f"`{ref}`: an eval case is not valid", _agent_errors(error)) from error
        failing = [r for r in evaluate_cases(prepared.specs[slug].spec, cases) if not r.passed]
        if failing:
            raise unprocessable(
                f"`{ref}`: {len(failing)} case(s) of the eval suite of `{slug}` fail on the plugin's own "
                "instructions",
                [
                    {
                        "loc": [ref, "cases", r.name],
                        "msg": r.error or f"missing {r.missing}, forbidden {r.forbidden}",
                    }
                    for r in failing
                ],
            )
        prepared.evals.append(f"{slug}/{Path(ref).stem}")
        prepared.files[ref] = path.read_bytes()


async def _procedures(
    session: AsyncSession, org_id: str, prepared: _Prepared, settings: Settings, replacing: OrgPlugin | None
) -> None:
    """Procedure ids no other procedure of the deployment or plugin of the organisation holds."""
    root, plugin = prepared.plugin.root, prepared.plugin
    taken = set(core_procedures.load(settings.procedures_extra or None).procedures)
    for row in await _rows(session, org_id):
        if replacing is None or row.id != replacing.id:
            taken |= set((row.details or {}).get("procedures") or {})
    for pattern in _contents(plugin).get("procedures", []):
        for path in sorted(root.glob(pattern)) if "*" in pattern else [inside(root, pattern)]:
            ref = path.relative_to(root).as_posix()
            proc = core_procedures.parse(_text(path, ref), ref, origin=f"{PREFIX}{plugin.name}")
            if proc.id in taken:
                raise conflict(f"`{ref}`: a procedure `{proc.id}` already exists on this platform")
            prepared.procedures[proc.id] = ref
            prepared.files[ref] = path.read_bytes()


# -- rows ------------------------------------------------------------------------------------------------


async def _rows(session: AsyncSession, org_id: str) -> list[OrgPlugin]:
    found = await session.execute(
        select(OrgPlugin).where(OrgPlugin.org_id == org_id).order_by(OrgPlugin.name)
    )
    return list(found.scalars())


async def installed(session: AsyncSession, org_id: str) -> list[OrgPlugin]:
    return await _rows(session, org_id)


async def installed_one(session: AsyncSession, org_id: str, name: str) -> OrgPlugin:
    row = await _row(session, org_id, name)
    if row is None:
        raise not_found("Plugin", name)
    return row


async def _store_files(session: AsyncSession, org_id: str, digest: str, files: dict[str, bytes]) -> None:
    present = set(
        (
            await session.execute(
                select(OrgPluginFile.path).where(
                    OrgPluginFile.org_id == org_id, OrgPluginFile.digest == digest
                )
            )
        ).scalars()
    )
    for path, content in sorted(files.items()):
        if path not in present:
            session.add(OrgPluginFile(org_id=org_id, digest=digest, path=path, content=content))
    await session.flush()


async def _drop_files(session: AsyncSession, org_id: str, digest: str) -> None:
    await session.execute(
        delete(OrgPluginFile).where(OrgPluginFile.org_id == org_id, OrgPluginFile.digest == digest)
    )


def _servers_and_status(plugin: Plugin) -> tuple[dict[str, Any], str]:
    """The servers as declared, and the row's status: `installed`, its servers provisioned with it
    (S43-06). `servers_pending` is what a row installed before S43-06 still says."""
    servers = {name: dict(doc) for name, doc in sorted(plugin.servers.items())}
    return servers, INSTALLED


# -- agents and skills -------------------------------------------------------------------------------------


async def _agent_and_versions(
    session: AsyncSession, org_id: str, slug: str
) -> tuple[Agent | None, list[AgentVersion]]:
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None:
        return None, []
    versions = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version)
        )
    ).scalars()
    return agent, list(versions)


def _diverged(versions: list[Any], plugin: str) -> bool:
    """A version the plugin did not write, or the backfilled mark of a hand-written text (S39-25)."""
    return any(not _ours(v.created_by, plugin) for v in versions) or (
        bool(versions) and (versions[0].created_by or "").endswith(DIVERGE)
    )


async def _skill_and_versions(
    session: AsyncSession, org_id: str, slug: str
) -> tuple[Skill | None, list[SkillVersion]]:
    skill = (
        await session.execute(select(Skill).where(Skill.org_id == org_id, Skill.slug == slug))
    ).scalar_one_or_none()
    if skill is None:
        return None, []
    versions = (
        await session.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id).order_by(SkillVersion.version)
        )
    ).scalars()
    return skill, list(versions)


async def _install_skill(
    session: AsyncSession, org_id: str, prepared: _Prepared, name: str, skipped: list[str]
) -> bool:
    """`True` when the skill is the plugin's (installed now, or earlier by it)."""
    skill, versions = await _skill_and_versions(session, org_id, name)
    if skill is None:
        await install_skills_and_agents(
            session, org_id, [prepared.skills[name]], [],
            provenance(prepared.name, name, prepared.plugin.version), what=f"the plugin `{prepared.name}`",
        )  # fmt: skip
        return True
    if versions and _ours(versions[0].created_by, prepared.name):
        return True
    skipped.append(name)
    return False


async def _install_agent(
    session: AsyncSession, org_id: str, prepared: _Prepared, slug: str, skipped: list[str]
) -> bool:
    agent, versions = await _agent_and_versions(session, org_id, slug)
    if agent is None:
        entry = prepared.agents[slug]
        await install_skills_and_agents(
            session, org_id, [], [entry.document], f"{PREFIX}{prepared.name}@{prepared.plugin.version}",
            what=f"the plugin `{prepared.name}`",
            catalogue_provenance={slug: provenance(prepared.name, slug, entry.version)},
        )  # fmt: skip
        return True
    if versions and _ours(versions[0].created_by, prepared.name):
        return True
    skipped.append(slug)
    return False


async def _upgrade_agent(
    session: AsyncSession, org_id: str, prepared: _Prepared, slug: str, principal: Principal
) -> str:
    """`published`, `unchanged`, `offered` (the organisation changed it) or `foreign` (not the plugin's)."""
    agent, versions = await _agent_and_versions(session, org_id, slug)
    if agent is None:
        return "absent"
    if not _ours(versions[0].created_by if versions else None, prepared.name):
        return "foreign"
    if _diverged(versions, prepared.name):
        return "offered"
    entry, spec = prepared.agents[slug], prepared.specs[slug].spec
    new_tags = [t for t in entry.tags if t not in (agent.tags or [])]
    agent.tags = [*(agent.tags or []), *new_tags][:MAX_TAGS]
    if versions[-1].checksum == digest(spec):
        return "unchanged"
    author = provenance(prepared.name, slug, entry.version)
    version = versions[-1].version + 1
    session.add(
        AgentVersion(
            agent_id=agent.id,
            org_id=org_id,
            version=version,
            spec=spec.model_dump(mode="json"),
            checksum=digest(spec),
            created_by=author,
            message=f"Plugin update to {prepared.name} {prepared.plugin.version} ({slug}@{entry.version})",
        )
    )
    await session.flush()
    await record(session, principal, "agent.publish", org_id=org_id, target_type="agent", target_id=agent.id,
                 version=version, via=author)  # fmt: skip
    await after_publish(session, principal, agent, version, author)
    return "published"


async def _upgrade_skill(session: AsyncSession, org_id: str, prepared: _Prepared, name: str) -> str:
    skill, versions = await _skill_and_versions(session, org_id, name)
    if skill is None:
        return "absent"
    if not versions or not _ours(versions[0].created_by, prepared.name):
        return "foreign"
    if _diverged(versions, prepared.name):
        return "offered"
    files = prepared.skills[name]
    if versions[-1].digest == skill_digest(files):
        return "unchanged"
    author = provenance(prepared.name, name, prepared.plugin.version)
    session.add(
        SkillVersion(
            skill_id=skill.id,
            org_id=org_id,
            version=versions[-1].version + 1,
            files=dict(files),
            digest=skill_digest(files),
            created_by=author,
            **platform_columns(files),
        )
    )
    await session.flush()
    await record(session, SYSTEM, "skill.publish", org_id=org_id, target_type="skill", target_id=skill.id,
                 version=versions[-1].version + 1, via=author)  # fmt: skip
    return "published"


async def _remove_agent(session: AsyncSession, org_id: str, plugin: str, slug: str) -> bool:
    """Deleted when the plugin wrote every version and no project pins it; kept otherwise, as the
    organisation's own agent. `True` when removed."""
    agent, versions = await _agent_and_versions(session, org_id, slug)
    if agent is None or not versions or not _ours(versions[0].created_by, plugin):
        return True
    pinned = (
        await session.execute(select(func.count()).where(ProjectAgent.agent_id == agent.id))
    ).scalar_one()
    if _diverged(versions, plugin) or pinned:
        agent.scope = "org"
        return False
    await session.execute(delete(Agent).where(Agent.id == agent.id))
    return True


async def _skills_in_use(session: AsyncSession, org_id: str) -> set[str]:
    specs = (await session.execute(select(AgentVersion.spec).where(AgentVersion.org_id == org_id))).scalars()
    return {
        str(s.get("slug")) for spec in specs for s in (spec or {}).get("skills") or [] if isinstance(s, dict)
    }


async def _remove_skill(session: AsyncSession, org_id: str, plugin: str, name: str, in_use: set[str]) -> bool:
    skill, versions = await _skill_and_versions(session, org_id, name)
    if skill is None or not versions or not _ours(versions[0].created_by, plugin):
        return True
    if _diverged(versions, plugin) or name in in_use:
        return False
    await session.execute(delete(Skill).where(Skill.id == skill.id))
    return True


# -- install, upgrade, uninstall -----------------------------------------------------------------------------


def _manifest_meta(plugin: Plugin) -> dict[str, Any]:
    return dict(plugin.manifest.get("metadata") or {})


async def _write(
    session: AsyncSession, org_id: str, prepared: _Prepared, principal: Principal
) -> tuple[PluginContents, dict[str, Any]]:
    """Skills, then agents, then the files; returns the contents and the row's details."""
    skipped: dict[str, list[str]] = {"skills": [], "agents": []}
    skills = [
        n
        for n in sorted(prepared.skills)
        if await _install_skill(session, org_id, prepared, n, skipped["skills"])
    ]
    agents = [
        s
        for s in sorted(prepared.agents)
        if await _install_agent(session, org_id, prepared, s, skipped["agents"])
    ]
    await _store_files(session, org_id, prepared.plugin.digest, prepared.files)
    servers, _ = _servers_and_status(prepared.plugin)
    contents = PluginContents(
        agents=agents,
        skills=skills,
        templates=[f"{PREFIX}{prepared.name}/{t}" for t in sorted(prepared.templates)],
        evals=sorted(prepared.evals),
        procedures=sorted(prepared.procedures),
        servers=sorted(servers),
    )
    details = {
        "templates": prepared.templates,
        "procedures": prepared.procedures,
        "servers": servers,
        "skipped": skipped,
        "clients": _clients(prepared),
    }
    return contents, details


def _clients(prepared: _Prepared) -> dict[str, dict[str, str | None]]:
    """An external client's `catalogue.client` and `catalogue.reach` (ADR 0040): which client the
    Integrations page offers it as, and whether it calls from its vendor's cloud."""
    return {
        slug: {"client": entry.client, "reach": entry.reach}
        for slug, entry in sorted(prepared.agents.items())
        if entry.kind == "external"
    }


class _Same:
    """`installed_by` not given: the principal's."""


_SAME = _Same()


async def check(
    session: AsyncSession, org_id: str, plugin: Plugin, settings: Settings, *, replacing: OrgPlugin | None
) -> None:
    """Every check an install (or an upgrade of `replacing`) runs against this organisation, nothing
    written: what a request waiting for a second approval must already pass."""
    await _prepare(session, org_id, plugin, settings, replacing=replacing)


async def install(
    session: AsyncSession,
    org_id: str,
    plugin: Plugin,
    source: str,
    settings: Settings,
    principal: Principal | None,
    *,
    default: bool = False,
    installed_by: str | _Same | None = _SAME,
) -> tuple[OrgPlugin, bool]:
    """Installs `plugin`; `(row, False)` without writing anything when the same content is already
    installed (the start-up reconcile replays it), 409 when another version is. `installed_by` is the
    principal's email unless given (an approved request records its requester; `None`, the platform)."""
    row = await _row(session, org_id, plugin.name)
    if row is not None:
        if row.digest == plugin.digest:
            return row, False
        raise conflict(f"`{plugin.name}` {row.version} is installed: upgrade it to move to {plugin.version}")
    who = principal or SYSTEM
    prepared = await _prepare(session, org_id, plugin, settings, replacing=None)
    contents, details = await _write(session, org_id, prepared, who)
    _, status = _servers_and_status(plugin)
    meta = _manifest_meta(plugin)
    row = OrgPlugin(
        org_id=org_id,
        name=plugin.name,
        source=source,
        version=plugin.version,
        digest=plugin.digest,
        manifest=dict(plugin.manifest),
        contents=contents.model_dump(),
        details=details,
        status=status,
        is_default=default,
        installed_by=(
            (principal.email if principal is not None else None)
            if isinstance(installed_by, _Same)
            else installed_by
        ),
        installed_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    await plugin_servers.provision(session, org_id, plugin, who, settings)
    await record(session, who, "plugin.install", org_id=org_id, target_type="plugin", target_id=plugin.name,
                 version=plugin.version, source=source, digest=plugin.digest, publisher=meta.get("publisher"),
                 skipped=details["skipped"], servers=contents.servers)  # fmt: skip
    return row, True


async def _upgrade_each(
    names: list[str],
    upgrade_one: Callable[[str], Awaitable[str]],
    install_one: Callable[[str], Awaitable[bool]],
    skipped: list[str],
    offered: list[str],
) -> list[str]:
    """The names that stay the plugin's after an upgrade; `offered` gets those the organisation changed."""
    taken: list[str] = []
    for name in names:
        outcome = await upgrade_one(name)
        if outcome == "absent":
            outcome = "published" if await install_one(name) else "foreign"
        if outcome == "foreign":
            if name not in skipped:
                skipped.append(name)
            continue
        if outcome == "offered":
            offered.append(name)
        taken.append(name)
    return taken


async def upgrade(
    session: AsyncSession,
    org_id: str,
    row: OrgPlugin,
    plugin: Plugin,
    settings: Settings,
    principal: Principal,
) -> tuple[str, list[str]]:
    """Moves `row` to `plugin`; returns the previous version and the agents offered the update."""
    previous = row.version
    if plugin.name != row.name:
        raise unprocessable(f"the source served `{plugin.name}` for `{row.name}`")
    if plugin.digest == row.digest:
        # Nothing to move, but a project born before the bases were delivered still receives them:
        # upgrading to the installed version is how an organisation repairs that (S51-06b).
        knowledge = await _projects_receive_template_knowledge(session, org_id, row.name)
        if any(done.get("created") or done.get("served") for done in knowledge.values()):
            await record(session, principal, "plugin.knowledge", org_id=org_id, target_type="plugin",
                         target_id=row.name, version=row.version, knowledge=knowledge)  # fmt: skip
        # And the agents and skills of this version the organisation does not have: a row written
        # by the S43-09 migration names only the agents the organisation had then (S59-06).
        await _install_what_is_missing(session, org_id, row, plugin, settings, principal)
        return previous, []
    prepared = await _prepare(session, org_id, plugin, settings, replacing=row)
    offered: list[str] = []
    kept: dict[str, list[str]] = {"agents": [], "skills": []}
    skipped: dict[str, list[str]] = {"skills": [], "agents": []}
    old = PluginContents.model_validate(row.contents or {})

    skills = await _upgrade_each(
        sorted(prepared.skills),
        lambda n: _upgrade_skill(session, org_id, prepared, n),
        lambda n: _install_skill(session, org_id, prepared, n, skipped["skills"]),
        skipped["skills"],
        kept["skills"],
    )
    agents = await _upgrade_each(
        sorted(prepared.agents),
        lambda s: _upgrade_agent(session, org_id, prepared, s, principal),
        lambda s: _install_agent(session, org_id, prepared, s, skipped["agents"]),
        skipped["agents"],
        offered,
    )

    for slug in sorted(set(old.agents) - set(agents)):
        if not await _remove_agent(session, org_id, row.name, slug):
            kept["agents"].append(slug)
    await session.flush()
    in_use = await _skills_in_use(session, org_id)
    for name in sorted(set(old.skills) - set(skills)):
        if not await _remove_skill(session, org_id, row.name, name, in_use):
            kept["skills"].append(name)

    await _store_files(session, org_id, plugin.digest, prepared.files)
    await _drop_files(session, org_id, row.digest)
    await plugin_servers.provision(session, org_id, plugin, principal, settings)
    servers, status = _servers_and_status(plugin)
    contents = PluginContents(
        agents=agents,
        skills=skills,
        templates=[f"{PREFIX}{plugin.name}/{t}" for t in sorted(prepared.templates)],
        evals=sorted(prepared.evals),
        procedures=sorted(prepared.procedures),
        servers=sorted(servers),
    )
    row.version, row.digest, row.manifest = plugin.version, plugin.digest, dict(plugin.manifest)
    row.contents = contents.model_dump()
    row.details = {
        "templates": prepared.templates,
        "procedures": prepared.procedures,
        "servers": servers,
        "skipped": skipped,
        "kept": kept,
        "clients": _clients(prepared),
    }
    row.status, row.installed_by, row.installed_at = status, principal.email, utcnow()
    await session.flush()
    knowledge = await _projects_receive_template_knowledge(session, org_id, row.name)
    from .offered_knowledge import upgrade_plugin

    await upgrade_plugin(session, org_id, row, principal)  # S63-06: the installations' offered knowledge
    await record(session, principal, "plugin.upgrade", org_id=org_id, target_type="plugin",
                 target_id=row.name, previous_version=previous, version=plugin.version,
                 digest=plugin.digest, offered=offered, kept=kept,
                 servers=contents.servers, knowledge=knowledge)  # fmt: skip
    return previous, offered


async def _install_what_is_missing(
    session: AsyncSession,
    org_id: str,
    row: OrgPlugin,
    plugin: Plugin,
    settings: Settings,
    principal: Principal,
) -> None:
    """The plugin's agents and skills that exist nowhere in the organisation, installed as an install
    would (S59-06). An agent or skill the organisation has under that name, the plugin's or its own, is
    left as it is: a repair adds, it never rewrites."""
    prepared = await _prepare(session, org_id, plugin, settings, replacing=row)
    contents = PluginContents.model_validate(row.contents or {})
    added: dict[str, list[str]] = {"skills": [], "agents": []}
    for name in sorted(prepared.skills):
        if name not in contents.skills and (await _skill_and_versions(session, org_id, name))[0] is None:
            await _install_skill(session, org_id, prepared, name, [])
            added["skills"].append(name)
    for slug in sorted(prepared.agents):
        if slug not in contents.agents and (await _agent_and_versions(session, org_id, slug))[0] is None:
            await _install_agent(session, org_id, prepared, slug, [])
            added["agents"].append(slug)
    if not (added["skills"] or added["agents"]):
        return
    row.contents = contents.model_copy(
        update={
            "skills": sorted({*contents.skills, *added["skills"]}),
            "agents": sorted({*contents.agents, *added["agents"]}),
        }
    ).model_dump()
    await _store_files(session, org_id, plugin.digest, prepared.files)
    await session.flush()
    await record(session, principal, "plugin.repair", org_id=org_id, target_type="plugin",
                 target_id=row.name, version=row.version, added=added)  # fmt: skip


async def _projects_receive_template_knowledge(
    session: AsyncSession, org_id: str, plugin: str
) -> dict[str, dict[str, list[str]]]:
    """The knowledge bases the upgraded templates ship (`defaults.knowledge`, S51-06), for the projects
    already born of them: a project is born once, so without this an agent the upgrade brings refuses
    every run on a base its project never received. The same idempotent path as a birth (created,
    completed, reused, served or left alone with the reason, ADR 0008). Returns what was done, by project."""
    from .template_knowledge import install_template_knowledge
    from .templates import delivery, template_manifest

    projects = (
        await session.execute(
            select(Project)
            .where(
                Project.org_id == org_id,
                Project.status != "archived",
                Project.template_ref.startswith(f"{PREFIX}{plugin}/", autoescape=True),
            )
            .order_by(Project.slug)
        )
    ).scalars()
    done: dict[str, dict[str, list[str]]] = {}
    for project in projects:
        template = str(project.template_ref)
        delivered = delivery(*await template_manifest(session, template, org_id))
        if delivered.knowledge:
            outcome = await install_template_knowledge(
                session, project, delivered.knowledge, delivered.inputs, f"template:{template}"
            )
            done[project.slug] = {k: v for k, v in outcome.items() if v}
    return done


async def uninstall(
    session: AsyncSession, org_id: str, row: OrgPlugin, principal: Principal, settings: Settings | None = None
) -> dict[str, Any]:
    """Removes what the plugin installed and nobody changed; returns what was removed and what kept."""
    for other in await _rows(session, org_id):
        required = (other.manifest.get("requires") or {}).get("plugins") or []
        names = {str(r.get("name") if isinstance(r, dict) else r) for r in required}
        if other.id != row.id and row.name in names:
            raise conflict(f"`{other.name}` requires `{row.name}`: uninstall it first")
    contents = PluginContents.model_validate(row.contents or {})
    removed: dict[str, list[str]] = {"agents": [], "skills": []}
    kept: dict[str, list[str]] = {"agents": [], "skills": []}
    for slug in contents.agents:
        (removed if await _remove_agent(session, org_id, row.name, slug) else kept)["agents"].append(slug)
    await session.flush()
    in_use = await _skills_in_use(session, org_id)
    for name in contents.skills:
        (removed if await _remove_skill(session, org_id, row.name, name, in_use) else kept)["skills"].append(
            name
        )
    await _drop_files(session, org_id, row.digest)
    # Its assistants stop (ADR 0133): what belongs to their users stays, nobody can converse on them.
    await session.execute(
        update(AssistantInstallation)
        .where(AssistantInstallation.org_id == org_id, AssistantInstallation.plugin == row.name)
        .values(status="suspended")
    )
    await plugin_servers.deprovision(session, org_id, row.name, principal, settings or get_settings())
    await session.delete(row)
    await session.flush()
    outcome = {"removed": removed, "kept": kept, "servers": contents.servers}
    await record(session, principal, "plugin.uninstall", org_id=org_id, target_type="plugin",
                 target_id=row.name, version=row.version, **outcome)  # fmt: skip
    return outcome


async def to_dto(row: OrgPlugin, supply: PluginSupply | None, *, wait: bool = True) -> InstalledPlugin:
    """`wait=False` (a list): the newest version a source offers is the one already known, never read
    from the network while the list waits."""
    meta = dict((row.manifest or {}).get("metadata") or {})
    latest = await supply.latest(row.source, row.name, wait=wait) if supply is not None else None
    if latest is not None and semver_key(latest) <= semver_key(row.version):
        latest = None
    return InstalledPlugin(
        name=row.name,
        version=row.version,
        source=row.source,
        digest=row.digest,
        publisher=str(meta.get("publisher", "")),
        license=str(meta.get("license", "")),
        description=str(meta.get("description", "")),
        default=row.is_default,
        contents=PluginContents.model_validate(row.contents or {}),
        installed_by=row.installed_by,
        installed_at=row.installed_at,
        latest=latest,
    )


# -- what the platform serves again: templates and procedures ------------------------------------------------


async def plugin_template(session: AsyncSession, org_id: str | None, ref: str) -> tuple[dict[str, Any], Path]:
    """`plugin:<plugin>/<template>`: the manifest and the directory of a template an installed plugin
    brought, with its files."""
    plugin, _, template = ref.removeprefix(PREFIX).partition("@")[0].partition("/")
    row = await _row(session, org_id, plugin) if org_id else None
    directory = ((row.details or {}).get("templates") or {}).get(template) if row is not None else None
    if row is None or directory is None:
        raise unprocessable(f"template `{ref}` not found: no installed plugin of the organisation brings it")
    folder = inside(await _materialise(session, row), str(directory))
    manifest = yaml.safe_load((folder / "manifest.yaml").read_text(encoding="utf-8")) or {}
    return dict(manifest), folder


async def plugin_procedures(
    session: AsyncSession, org_slugs: set[str]
) -> dict[str, core_procedures.Procedure]:
    """The procedures of the plugins installed in these organisations (ADR 0067 as amended by 0093)."""
    if not org_slugs:
        return {}
    rows = (
        await session.execute(
            select(OrgPlugin, OrgPluginFile)
            .join(Organization, Organization.id == OrgPlugin.org_id)
            .join(
                OrgPluginFile,
                (OrgPluginFile.org_id == OrgPlugin.org_id) & (OrgPluginFile.digest == OrgPlugin.digest),
            )
            .where(Organization.slug.in_(sorted(org_slugs)))
            .order_by(Organization.slug, OrgPlugin.name, OrgPluginFile.path)
        )
    ).tuples()
    found: dict[str, core_procedures.Procedure] = {}
    for row, stored in rows:
        by_path = {path: proc_id for proc_id, path in ((row.details or {}).get("procedures") or {}).items()}
        proc_id = by_path.get(stored.path)
        if proc_id is None or proc_id in found:
            continue
        try:
            found[proc_id] = core_procedures.parse(
                stored.content.decode("utf-8"), stored.path, origin=f"{PREFIX}{row.name}"
            )
        except (core_procedures.ProcedureError, UnicodeDecodeError):
            continue
    return found


async def org_id_of(session: AsyncSession, slug: str) -> str:
    found = (
        await session.execute(select(Organization.id).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if found is None:
        raise not_found("Organisation", slug)
    return str(found)


__all__ = [
    "INSTALLED",
    "PREFIX",
    "SERVERS_PENDING",
    "check",
    "install",
    "installed",
    "installed_one",
    "installed_row",
    "org_id_of",
    "plugin_assistant",
    "plugin_procedures",
    "plugin_template",
    "plugin_text",
    "provenance",
    "to_dto",
    "uninstall",
    "upgrade",
]
