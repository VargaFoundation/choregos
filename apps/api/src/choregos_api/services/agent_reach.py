# SPDX-License-Identifier: Apache-2.0
"""What an agent version reaches, in one place (ADR 0127).

A run's tool listing (`getRunTools`), its tool call (`callRunTool`) and the access endpoint
(`getAgentAccess`) all read `tool_reach`: the console cannot show what a run would not get.

A source whose list is PRESENT on the version is closed: the run is offered what the version names
AND what the project and the deployment allow, never more. An ABSENT list keeps the behaviour of a
version published before ADR 0127 (the project's). The frame (`choregos` server) and the connectors
the version names (`mcp_servers`, already closed by the broker) are not touched by the lists.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from choregos_core import ModelResolutionError, ModelResolver
from choregos_core.models import BY_SIZE
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..catalogue import catalogue, expand_names, project_tools
from ..db.models import (
    Agent,
    AgentVersion,
    KnowledgeBase,
    ModelProfileRow,
    Project,
    ProjectAgent,
    Run,
)
from ..knowledge.access import served_to_project
from ..schemas.agents import (
    A2aRuntime,
    AgentAccess,
    AgentAccessItem,
    AgentAccessModel,
    AgentAccessTool,
    AgentComponent,
    AgentSpec,
    VoiceRuntime,
)
from . import web_fetch, web_search
from .agent_releases import served
from .broker import BrokerTool, broker_tools, selection_of_spec, tools_of_selection
from .extension_tools import extension_access, extension_tools

ExtensionTools = Callable[
    [AsyncSession, Run, Project, set[str]], Awaitable[dict[str, tuple[str, dict[str, Any]]]]
]


@dataclass
class ToolReach:
    """The tools a run of a version is offered, by source, and what the version names but will not get."""

    catalogue: list[Any] = field(default_factory=list)
    extensions: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)
    brokered: dict[str, BrokerTool] = field(default_factory=dict)
    fetch: bool = False
    search: bool = False
    #: The sources the version closes (`catalogue`, `extensions`, `web`).
    closed: frozenset[str] = frozenset()
    refused: list[AgentAccessTool] = field(default_factory=list)


async def spec_of_run(session: AsyncSession, run: Run, project: Project) -> dict[str, Any] | None:
    """The stored spec of the version a run of a registry agent runs; None for a run with no registry
    agent (a playbook, a job step, a trial), whose tools are the project's as before."""
    if not run.agent_slug or not run.agent_version:
        return None
    return (
        await session.execute(
            select(AgentVersion.spec)
            .join(Agent, Agent.id == AgentVersion.agent_id)
            .where(
                Agent.org_id == project.org_id,
                Agent.slug == run.agent_slug,
                AgentVersion.version == run.agent_version,
            )
        )
    ).scalar_one_or_none()


def _tools_of(spec: dict[str, Any] | None) -> dict[str, Any]:
    tools = (spec or {}).get("tools")
    return tools if isinstance(tools, dict) else {}


def _refusal(name: str, source: str, reason: str) -> AgentAccessTool:
    return AgentAccessTool.model_validate(
        {"name": name, "source": source, "origin": "refused", "reason": reason}
    )


def _why_not_catalogue(name: str, config: dict[str, Any]) -> str:
    tool = next((o for o in catalogue().tools if o.name == name), None)
    if tool is None:
        return "not in the deployment catalogue"
    if tool.may_write:
        return "may write to a third party: only governed actions do"
    groups = [str(g) for g in config.get("groups") or []]
    if not tool.open_to(groups):
        return "reserved for groups the project does not belong to"
    return "the project's `tools` does not open it"


async def tool_reach(
    session: AsyncSession,
    run: Run,
    project: Project,
    spec: dict[str, Any] | None,
    *,
    list_extensions: ExtensionTools = extension_tools,
    include_writers: bool = False,
) -> ToolReach:
    """What a run of `project` is offered given the stored `spec` of its agent version.

    `spec` None (no registry agent): the project's behaviour, nothing closed. `include_writers` is the
    direct call's (it answers a refused writer with the way out); the listing never offers a writer."""
    tools = _tools_of(spec)
    config = project.config or {}
    allowed = [str(t) for t in config.get("tools") or []]
    groups = [str(g) for g in config.get("groups") or []]
    reach = ToolReach(
        closed=frozenset(k for k in ("catalogue", "extensions", "web") if tools.get(k) is not None)
    )

    offered = project_tools(allowed, groups, include_writers=include_writers)
    if "catalogue" in reach.closed:
        names, groupless = expand_names([str(t) for t in tools["catalogue"]])
        reach.refused = [
            _refusal(g, "catalogue", "no tool of the deployment catalogue is in this group")
            for g in groupless
        ]
        reach.catalogue = [o for o in offered if o.name in names]
        got = {o.name for o in reach.catalogue}
        reach.refused += [
            _refusal(n, "catalogue", _why_not_catalogue(n, config)) for n in sorted(names - got)
        ]
    else:
        reach.catalogue = offered

    listed = await list_extensions(session, run, project, {o.name for o in reach.catalogue})
    if "extensions" in reach.closed:
        named = {str(e) for e in tools["extensions"]}
        reach.extensions = {n: (e, t) for n, (e, t) in listed.items() if e in named}
        from ..extensions import tool_providers

        installed = set(tool_providers())
        reach.refused += [
            _refusal(e, "extension", "no such extension is installed") for e in sorted(named - installed)
        ]
    else:
        reach.extensions = listed

    if spec is not None:
        reach.brokered = await tools_of_selection(session, selection_of_spec(spec), project)
    else:
        reach.brokered = await broker_tools(session, run, project)

    web_named = {str(w) for w in tools["web"]} if "web" in reach.closed else {"fetch", "search"}
    for kind, module in (("fetch", web_fetch), ("search", web_search)):
        if kind not in web_named:
            continue
        ok = await (module.offered(session, run) if kind == "fetch" else module.offered(session, project))
        if kind == "fetch":
            reach.fetch = ok
        else:
            reach.search = ok
        if not ok and "web" in reach.closed:
            why = (
                "the run's network allows no host"
                if kind == "fetch"
                else "no search backend serves the project"
            )
            reach.refused.append(_refusal(module.TOOL_NAME, "web", why))
    return reach


# ───────────────────────────── the access endpoint ─────────────────────────────


async def version_of_project(session: AsyncSession, agent: Agent, project: Project) -> int:
    """The version a run of `project` takes: its pin, else its channel, else production (ADR 0071)."""
    pin = (
        await session.execute(
            select(ProjectAgent).where(
                ProjectAgent.project_id == project.id, ProjectAgent.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    version, _, _ = served(
        agent, pin=pin, project_channel=project.agent_channel or "stable", work_item_id=None
    )
    if version is not None:
        return version
    latest = (
        await session.execute(
            select(AgentVersion.version)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return latest or 1


def _inherits_and_complete(spec: AgentSpec) -> tuple[list[AgentComponent], bool]:
    """ADR 0127 §7: what the version leaves to its project, and whether that is nothing."""
    if isinstance(spec.runtime, A2aRuntime):
        return [], True
    inherits: list[AgentComponent] = []
    if not spec.model:
        inherits.append("model")
    if isinstance(spec.runtime, VoiceRuntime):
        return inherits, not inherits
    tools = spec.tools
    if tools is None or tools.catalogue is None or tools.extensions is None or tools.web is None:
        inherits.append("tools")
    if spec.memories is None:
        inherits.append("memory")
    return inherits, not inherits


async def _model_of(session: AsyncSession, spec: AgentSpec, project: Project) -> AgentAccessModel:
    from .definitions import project_config
    from .model_catalogue import load_policy

    requested = spec.model or BY_SIZE
    origin = "agent" if spec.model else "project"
    rows = (
        (await session.execute(select(ModelProfileRow).where(ModelProfileRow.scope == "platform")))
        .scalars()
        .all()
    )
    resolver = ModelResolver(
        gateway_url="",
        gateway_models=[],
        validated_backends={r.litellm_model: list(r.validated_backends or []) for r in rows},
        catalogue=await load_policy(session, project.org_id),
    )
    config = project_config(project)
    try:
        resolved = resolver.resolve(
            requested, project=config, size="M", backend=spec.backend or config.agent.default_backend
        )
    except ModelResolutionError as error:
        return AgentAccessModel.model_validate(
            {"requested": requested, "origin": "refused", "reason": str(error)}
        )
    reason = None if spec.model else "the workflow actor's `model`, else the platform's size profile"
    return AgentAccessModel.model_validate(
        {"requested": requested, "resolved": resolved.litellm_model, "origin": origin, "reason": reason}
    )


async def _knowledge_of(session: AsyncSession, spec: AgentSpec, project: Project) -> list[AgentAccessItem]:
    items: list[AgentAccessItem] = []
    for ref in spec.knowledge:
        base = (
            await session.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.org_id == project.org_id, KnowledgeBase.slug == ref.slug
                )
            )
        ).scalar_one_or_none()
        reason = None
        if base is None:
            reason = "no such knowledge base in the organisation"
        elif not served_to_project(base, project.slug):
            reason = f"not served to the project (scope `{base.scope}`, status `{base.status}`)"
        items.append(
            AgentAccessItem.model_validate(
                {
                    "name": ref.slug,
                    "origin": "agent" if reason is None else "refused",
                    "mode": getattr(base, "mode", None),
                    "reason": reason,
                }
            )
        )
    return items


async def access_of(
    session: AsyncSession, agent: Agent, version: int, spec: AgentSpec, project: Project
) -> AgentAccess:
    """What `version` of `agent` reaches in `project`, from the function a run's tool listing reads."""
    stored = spec.model_dump(mode="json")
    transient = Run(
        id="access-preview",
        project_id=project.id,
        agent_slug=agent.slug,
        agent_version=version,
        stage_input={},
    )
    inherits, complete = _inherits_and_complete(spec)
    remote = isinstance(spec.runtime, A2aRuntime)
    voice = isinstance(spec.runtime, VoiceRuntime)

    tools: list[AgentAccessTool] = []
    if not remote:
        from choregos_tools_mcp.tools import TOOL_SCHEMAS

        reach = await tool_reach(session, transient, project, stored)
        if voice:
            reach = ToolReach(brokered=reach.brokered)
        tools += [
            AgentAccessTool.model_validate({"name": str(t["name"]), "source": "frame", "origin": "frame"})
            for t in TOOL_SCHEMAS
        ]

        def origin(source: str) -> str:
            return "agent" if source in reach.closed else "project"

        for tool in reach.catalogue:
            tools.append(_ok(tool.name, "catalogue", origin("catalogue"), "read"))
        for name, (extension, tool_) in reach.extensions.items():
            tools.append(_ok(name, "extension", origin("extensions"), extension_access(extension, tool_)))
        for name, brokered in reach.brokered.items():
            tools.append(_ok(name, "connector", "agent", brokered.access))
        for flag, name in ((reach.fetch, web_fetch.TOOL_NAME), (reach.search, web_search.TOOL_NAME)):
            if flag:
                tools.append(_ok(name, "web", origin("web"), "read"))
        tools += reach.refused
    return AgentAccess(
        agent=agent.slug,
        version=version,
        project=project.slug,
        complete=complete,
        inherits=inherits,
        model=await _model_of(session, spec, project),
        tools=tools,
        knowledge=[] if remote else await _knowledge_of(session, spec, project),
        memories=[]
        if remote or voice
        else [
            AgentAccessItem(
                name=f"{m.scope}:{m.slug}",
                origin="agent" if spec.memories is not None else "project",
                mode=m.mode,
            )
            for m in spec.effective_memories()
        ],
    )


def _ok(name: str, source: str, origin: str, access: str) -> AgentAccessTool:
    return AgentAccessTool.model_validate(
        {"name": name, "source": source, "origin": origin, "access": access}
    )
