# SPDX-License-Identifier: Apache-2.0
"""The workflow catalogue (ADR 0108): what an organisation can adopt, and adopting it.

Four sources, listed together: the core's workflows (`core`), the workflows of the deployment's templates
(`template:<name>`), those of the templates its installed plugins brought (`plugin:<name>`, the external
catalogues: signed, pinned, through the supply-chain gate) and the ones it shared from its projects
(`org`). What an entry requires is computed by the engine (`exigences`), never declared; each agent it
names is resolved in the organisation. Adopting copies the YAML into a project and installs the catalogue
agents it names — never a plugin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
import yaml
from choregos_contracts import AgentActor, HumanActor, Workflow
from choregos_core import ValidationError as WorkflowInvalid
from choregos_core import parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, template_yaml
from choregos_core.dsl.exigences import exigences
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, AgentVersion, OrgWorkflow
from ..errors import ApiError
from ..schemas.definitions import (
    WorkflowCatalogueAgentDto,
    WorkflowCatalogueEntryDto,
    WorkflowCatalogueRequirementDto,
)

logger = structlog.get_logger(__name__)

CORE = "core"
ORG = "org"
#: Agent scopes a workflow of any project may name (ADR 0061): not a personal or a project's agent.
SHARED_SCOPES = ("org", "platform")


@dataclass(frozen=True)
class Offered:
    """One entry: its source, its YAML as offered, and the parsed workflow."""

    source: str
    name: str
    version: int
    yaml: str
    workflow: Workflow
    shared_by: str | None = None
    shared_from: str | None = None
    shared_at: datetime | None = None

    @property
    def origin(self) -> str:
        """What an adopted workflow records (`workflow_defs.origin`)."""
        return f"{self.source}/{self.name}@{self.version}"


@dataclass(frozen=True)
class Resolution:
    resolution: str
    plugin: str | None = None
    display_name: str | None = None


def parse_origin(origin: str) -> tuple[str, str, int] | None:
    """`<source>/<name>@<version>` → its parts; None for anything else."""
    head, _, version = origin.rpartition("@")
    source, _, name = head.rpartition("/")
    if not source or not name or not version.isdigit():
        return None
    return source, name, int(version)


def _parse(source: str, text: str) -> Workflow | None:
    try:
        workflow, report = parse_workflow(text, strict=False)
    except (WorkflowInvalid, yaml.YAMLError):
        workflow, report = None, None
    if workflow is None or report is None or not report.valid:
        # A broken entry is left out rather than offered: adopting it would be refused anyway.
        logger.warning("workflow_catalogue.invalid_entry", source=source)
        return None
    return workflow


def _offered(source: str, text: str, **shared: Any) -> Offered | None:
    workflow = _parse(source, text)
    if workflow is None:
        return None
    version = int(shared.pop("version", workflow.metadata.version))
    return Offered(source, workflow.metadata.name, version, text, workflow, **shared)


def _core() -> list[Offered]:
    return [o for name in TEMPLATE_NAMES if (o := _offered(CORE, template_yaml(name))) is not None]


def _template_entries(source: str, manifest: dict[str, Any], folder: Any) -> list[Offered]:
    """A template's own workflow files; a `template:<core>` reference is already a `core` entry."""
    from .gabarits import shipped_workflows

    out: list[Offered] = []
    for ref, text in shipped_workflows(manifest, folder):
        if str(ref).startswith("template:"):
            continue
        if (o := _offered(source, text)) is not None:
            out.append(o)
    return out


def _templates() -> list[Offered]:
    from .gabarits import repertoire_des_gabarits

    root = repertoire_des_gabarits()
    out: list[Offered] = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        manifest_path = folder / "manifest.yaml"
        if not manifest_path.is_file():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            out += _template_entries(f"template:{folder.name}", dict(manifest), folder)
        except (ApiError, OSError, yaml.YAMLError):
            logger.warning("workflow_catalogue.unreadable_template", template=folder.name)
    return out


async def _plugins(session: AsyncSession, org_id: str) -> list[Offered]:
    from .plugin_installer import installed, plugin_template

    out: list[Offered] = []
    for row in await installed(session, org_id):
        for template in sorted(((row.details or {}).get("templates") or {}).keys()):
            try:
                manifest, folder = await plugin_template(session, org_id, f"plugin:{row.name}/{template}")
                out += _template_entries(f"plugin:{row.name}", manifest, folder)
            except (ApiError, OSError, yaml.YAMLError):
                logger.warning(
                    "workflow_catalogue.unreadable_plugin_template", plugin=row.name, template=template
                )
    return out


async def _org(session: AsyncSession, org_id: str) -> list[Offered]:
    """The highest share of each name."""
    latest = (
        select(OrgWorkflow.name, func.max(OrgWorkflow.version).label("version"))
        .where(OrgWorkflow.org_id == org_id)
        .group_by(OrgWorkflow.name)
        .subquery()
    )
    rows = (
        await session.execute(
            select(OrgWorkflow)
            .join(latest, (OrgWorkflow.name == latest.c.name) & (OrgWorkflow.version == latest.c.version))
            .where(OrgWorkflow.org_id == org_id)
            .order_by(OrgWorkflow.name)
        )
    ).scalars()
    out: list[Offered] = []
    for row in rows:
        offered = _offered(
            ORG,
            row.yaml,
            version=row.version,
            shared_by=row.shared_by,
            shared_from=row.shared_from,
            shared_at=row.created_at,
        )
        if offered is not None:
            out.append(offered)
    return out


async def offered(session: AsyncSession, org_id: str) -> list[Offered]:
    """Every entry, in the order of the sources."""
    return [*_core(), *_templates(), *await _plugins(session, org_id), *await _org(session, org_id)]


async def find(session: AsyncSession, org_id: str, source: str, name: str) -> Offered | None:
    return next((o for o in await offered(session, org_id) if o.source == source and o.name == name), None)


def named_agents(workflow: Workflow) -> list[tuple[str, str, int | None]]:
    """(actor, slug, pinned version) of each agent the workflow names, in its order."""
    out: list[tuple[str, str, int | None]] = []
    for actor, spec in workflow.actors.items():
        if isinstance(spec, AgentActor) and spec.agent:
            slug, _, version = str(spec.agent).partition("@")
            out.append((actor, slug, int(version) if version else None))
    return out


async def resolutions(session: AsyncSession, org_id: str, slugs: set[str]) -> dict[str, Resolution]:
    """Where each slug resolves in the organisation, in the order of ADR 0040 §4: its own agent (a plugin's,
    a remote one), else the platform's catalogue (installed on adoption), else nowhere."""
    from .catalogue_d_agents import catalogue_of

    if not slugs:
        return {}
    agents = {
        a.slug: a
        for a in (
            await session.execute(
                select(Agent).where(
                    Agent.org_id == org_id, Agent.slug.in_(slugs), Agent.scope.in_(SHARED_SCOPES)
                )
            )
        ).scalars()
    }
    authors: dict[str, str | None] = {}
    if agents:
        first = (
            await session.execute(
                select(AgentVersion.agent_id, AgentVersion.created_by)
                .where(AgentVersion.agent_id.in_([a.id for a in agents.values()]))
                .order_by(AgentVersion.version)
            )
        ).all()
        for agent_id, created_by in first:
            authors.setdefault(agent_id, created_by)
    # The organisation's catalogue (S43-09): the offline plugins, each replaced by its installed version.
    view = await catalogue_of(session, org_id)
    out: dict[str, Resolution] = {}
    for slug in slugs:
        agent = agents.get(slug)
        if agent is not None:
            author = authors.get(agent.id) or ""
            if agent.kind == "remote":
                out[slug] = Resolution("remote", display_name=agent.display_name)
            elif author.startswith("plugin:"):
                plugin = author.removeprefix("plugin:").partition("/")[0].partition("@")[0]
                out[slug] = Resolution("plugin", plugin=plugin, display_name=agent.display_name)
            else:
                out[slug] = Resolution("org", display_name=agent.display_name)
            continue
        found = view.entry(slug)
        if found is not None and found.genre == "internal":
            out[slug] = Resolution("catalogue", display_name=str(found.document.get("display_name") or slug))
        else:
            out[slug] = Resolution("unresolved")
    missing = {slug for slug, r in out.items() if r.resolution == "unresolved"}
    if missing:
        # Which plugin of a configured source ships it (§2): the administrator's gesture, named.
        from ..config import get_settings
        from .plugin_supply import plugin_supply

        for slug, shipped in (await plugin_supply(get_settings()).shipping(missing)).items():
            out[slug] = Resolution(
                "unresolved", plugin=f"{shipped.name} {shipped.version} ({shipped.source})"
            )
    return out


def entry_dto(o: Offered, resolved: dict[str, Resolution]) -> WorkflowCatalogueEntryDto:
    meta = o.workflow.metadata
    info = meta.catalogue
    requires = exigences([o.workflow])
    agents = []
    for actor, slug, version in named_agents(o.workflow):
        r = resolved.get(slug, Resolution("unresolved"))
        agents.append(
            WorkflowCatalogueAgentDto(
                actor=actor,
                slug=slug,
                version=version,
                resolution=r.resolution,
                plugin=r.plugin,
                display_name=r.display_name,
            )
        )
    groups = sorted({spec.group for spec in o.workflow.actors.values() if isinstance(spec, HumanActor)})
    return WorkflowCatalogueEntryDto(
        source=o.source,
        name=o.name,
        version=o.version,
        title=(info.title if info and info.title else o.name),
        summary=(info.summary if info and info.summary else (meta.description or "")),
        tags=list(info.tags) if info else [],
        requires=[
            WorkflowCatalogueRequirementDto(capability=e.capacite, reasons=list(e.raisons)) for e in requires
        ],
        needs_repository=any(e.capacite == "scm" for e in requires),
        groups=groups,
        agents=agents,
        yaml=o.yaml,
        shared_by=o.shared_by,
        shared_from=o.shared_from,
        shared_at=o.shared_at,
    )


async def entries(session: AsyncSession, org_id: str) -> list[WorkflowCatalogueEntryDto]:
    listed = await offered(session, org_id)
    slugs = {slug for o in listed for _, slug, _ in named_agents(o.workflow)}
    resolved = await resolutions(session, org_id, slugs)
    return [entry_dto(o, resolved) for o in listed]


def renamed(text: str, name: str) -> str:
    """The same YAML with `metadata.name` set to `name`, every other byte kept (comments, style)."""
    root = yaml.compose(text)
    if not isinstance(root, yaml.MappingNode):
        return text
    metadata = next((v for k, v in root.value if getattr(k, "value", None) == "metadata"), None)
    if not isinstance(metadata, yaml.MappingNode):
        return text
    node = next((v for k, v in metadata.value if getattr(k, "value", None) == "name"), None)
    if not isinstance(node, yaml.ScalarNode):
        return text
    return text[: node.start_mark.index] + name + text[node.end_mark.index :]


__all__ = [
    "CORE",
    "ORG",
    "Offered",
    "Resolution",
    "entries",
    "entry_dto",
    "find",
    "named_agents",
    "offered",
    "parse_origin",
    "renamed",
    "resolutions",
]
