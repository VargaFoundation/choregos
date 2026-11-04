# SPDX-License-Identifier: Apache-2.0
"""What a stage announces to the agent: MCP servers, skills, knowledge bases and production sources.

Helpers of `stage`, which re-exports them; none of them is a Temporal activity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog
from choregos_contracts import (
    KnowledgeRef,
    McpServerRef,
    SkillRef,
    StageMemory,
)
from choregos_core.memory_bindings import Binding, legacy_scope
from sqlalchemy import select

logger = structlog.get_logger("choregos.stage")


def _mcp_servers(settings: Any, memory_scope: str = "project") -> dict[str, McpServerRef]:
    """The MCP servers announced to the agent: the ones that exist, and only those.

    Both were hard-coded, and both were wrong on the dev tenant of 2026-09-27: the tools sidecar
    only exists under the `tekton` executor, and the memory URL was built with
    `gateway_url.replace("4000", "8432")`, a port replacement that only holds if the gateway and
    the memory share a host. There, the gateway is `http://litellm:4000`: the memory became
    `http://litellm:8432`, measured without an answer in eight seconds, when
    `http://ecphoria:8432/mcp` answers in nine milliseconds.
    """
    servers: dict[str, McpServerRef] = {}
    if url := settings.tools_sidecar_url:
        servers["choregos"] = McpServerRef(url=url)
    # The memory server serves the PROJECT's shared memory: a run whose bindings are anything else (its
    # own memory, none, several, a global one) does not get it — it would read and write past its
    # bindings (S32-02, S40-11).
    if settings.memory_url and memory_scope == "project":
        servers["memory"] = McpServerRef(url=f"{settings.memory_url.rstrip('/')}/mcp")
    return servers


async def _gateway_catalogue(bundle: Any) -> list[Any]:
    """The models the project's gateway declares, so that the resolver sees behind an alias.

    Without them `provider_model` stayed empty, and the runner's `claude-code` refused every run on
    `platform/*`: it only takes a model it can see is a Claude (S28-02, 2026-10-12). A gateway that
    cannot list its models degrades to the old behaviour — no catalogue, no check — rather than
    stopping the stage.
    """
    try:
        return list(await bundle.adapters.gateway.list_models())
    except Exception as error:  # the catalogue sharpens the resolution, it is not a precondition
        logger.warning("gateway catalogue unavailable", error=str(error)[:300])
        return []


async def _agent_skills(session: Any, bundle: Any, agent: Any) -> list[SkillRef]:
    """The skills the agent's version carries: their version (given, else the latest) and their
    fingerprint, which the runner will verify. A skill absent from the library stops the run."""
    if agent is None or not agent.spec.skills:
        return []
    from choregos_api.db.models import Skill, SkillVersion
    from temporalio.exceptions import ApplicationError

    refs: list[SkillRef] = []
    for requested in agent.spec.skills:
        query = (
            select(SkillVersion)
            .join(Skill, Skill.id == SkillVersion.skill_id)
            .where(Skill.org_id == bundle.project.org_id, Skill.slug == requested.slug)
        )
        if requested.version is not None:
            query = query.where(SkillVersion.version == requested.version)
        else:
            # Unpinned: the latest APPROVED version — never one waiting for its second reviewer
            # (S32-06, ADR 0050). An activity, not workflow code: no replay concern.
            query = query.where(SkillVersion.review_status == "approved")
        row = (
            await session.execute(query.order_by(SkillVersion.version.desc()).limit(1))
        ).scalar_one_or_none()
        if row is not None and row.review_status != "approved":
            raise ApplicationError(
                f"the agent `{agent.agent.slug}` pins the skill `{requested.slug}` v{row.version}, "
                "which is high risk and waits for a second reviewer",
                type="SkillPendingReview",
                non_retryable=True,
            )
        if row is None and requested.version is None and await _skill_exists(session, bundle, requested.slug):
            raise ApplicationError(
                f"the agent `{agent.agent.slug}` carries the skill `{requested.slug}`, "
                "which has no approved version yet",
                type="SkillPendingReview",
                non_retryable=True,
            )
        if row is None:
            raise ApplicationError(
                f"the agent `{agent.agent.slug}` carries the skill `{requested.slug}`, "
                "which is not in the library",
                type="SkillMissing",
                non_retryable=True,
            )
        refs.append(SkillRef(slug=requested.slug, version=row.version, digest=row.digest))
    return refs


async def _skill_exists(session: Any, bundle: Any, slug: str) -> bool:
    from choregos_api.db.models import Skill

    trouvee = await session.execute(
        select(Skill.id).where(Skill.org_id == bundle.project.org_id, Skill.slug == slug)
    )
    return trouvee.first() is not None


async def _knowledge_of_agent(session: Any, bundle: Any, agent: Any, item: Any) -> list[KnowledgeRef]:
    """The knowledge bases the agent version reads (ADR 0051), pinned for this run. A base that does
    not exist, is not served to this project, or is restricted and not readable by the person who
    started the work item (ADR 0097), stops the run: it would answer without its reference."""
    if agent is None or not agent.spec.knowledge:
        return []
    from choregos_api.knowledge.on_behalf import OnBehalf
    from choregos_api.knowledge.serving import KnowledgeRefusedError, pin
    from choregos_core.restricted import mark
    from temporalio.exceptions import ApplicationError

    on_behalf = OnBehalf(session, bundle.project.org_id, bundle.project.id, item.initiated_by)
    try:
        refs = await pin(
            session,
            bundle.project.org_id,
            bundle.project.slug,
            [ref.slug for ref in agent.spec.knowledge],
            on_behalf,
        )
    except KnowledgeRefusedError as refusal:
        raise ApplicationError(
            f"the agent `{agent.agent.slug}` reads a knowledge base it cannot be served: {refusal}",
            type="KnowledgeRefused",
            non_retryable=True,
        ) from refusal
    # What this run writes lands under the work item: it is now read only by the bases' readers (ADR 0114).
    mark(item, on_behalf.restricted_served)
    return refs


#: What the name of an agent's MCP server must contain to count as a production indicator source (the
#: same cue as the `devops` agent: Loki, Prometheus, Grafana...).
_OBSERVABILITY_MCP_NAMES = ("grafana", "prometheus", "loki", "datadog", "sentry", "newrelic", "honeycomb")


def production_indicator_sources(config: Any, agent: Any = None) -> list[str]:
    """The production indicator sources THIS run can read, by name (S22-48).

    The production verifier demanded before/after figures for errors and latency, and a project with
    no source of them (the dev tenant: demo CD, no metrics) was refused every time, with smoke_ok
    measured true. Its instructions now say what to demand from what the project can measure; the
    platform tells it, the agent does not guess.

    Two sources, the ones a run actually reaches: the deployment catalogue's tools the project may
    call and that the deployment files under `observability`; the agent version's MCP servers named
    after an observability tool. Empty = none.
    """
    from choregos_api.catalogue import project_tools

    sources = {
        tool.name
        for tool in project_tools(list(config.tools or []), list(config.groups or []))
        if "observability" in tool.categories
    }
    servers = getattr(getattr(agent, "spec", None), "mcp_servers", None) or []
    sources |= {
        s.connector for s in servers if any(m in s.connector.lower() for m in _OBSERVABILITY_MCP_NAMES)
    }
    return sorted(sources)


#: Why every binding of a run is skipped when the project's policy turns memory off (S51-01).
MEMORY_DISABLED = "the project's policy disables memory (memory.enabled: false)"


@dataclass(slots=True)
class ResolvedMemories:
    """The memories a run reads and writes, resolved for its project (S40-11, ADR 0075)."""

    memories: list[StageMemory]
    #: Bindings that did not resolve: `{memory, reason}`; the run starts without them (ADR 0007).
    skipped: list[dict[str, str]]

    @property
    def legacy_scope(self) -> str | None:
        return legacy_scope([Binding(m.scope, m.slug, m.mode) for m in self.memories])


async def _memory_bindings(session: Any, bundle: Any, agent: Any) -> ResolvedMemories:
    """Turn the agent version's effective bindings into the memories THIS run reads and writes.

    A global memory must exist, be active and reach the run's project; a named project memory must
    exist and be active in the run's project; `shared` and `own` always resolve (`own` needs an agent).
    What does not resolve is skipped with its reason, never fatal: memory never blocks a stage
    (ADR 0007). A playbook run, with no agent, is `[project:shared write]`, as before.
    """
    from choregos_api.db.models import MemoryStore
    from choregos_api.services.memory_access import reaches_project
    from choregos_core.memory_bindings import OWN, SHARED, effective_bindings, namespace_of_binding
    from sqlalchemy import select

    spec = getattr(agent, "spec", None)
    bindings = (
        effective_bindings(None, "project")
        if spec is None
        else [Binding(b.scope, b.slug, b.mode) for b in spec.effective_memories()]
    )
    if not bundle.engine.memory_enabled():
        # The project's policy is authoritative (S51-01): a run of a project whose policy says
        # `memory.enabled: false` binds nothing, whatever its agent asks for, so it gets neither a
        # memory to write nor the memory server. Each binding is journaled as skipped, never fatal.
        return ResolvedMemories([], [{"memory": b.label, "reason": MEMORY_DISABLED} for b in bindings])
    agent_slug = agent.agent.slug if agent is not None else None
    stores = {
        (store.scope, store.owner_key, store.slug): store
        for store in (
            await session.execute(select(MemoryStore).where(MemoryStore.org_id == bundle.project.org_id))
        ).scalars()
    }
    resolved: list[StageMemory] = []
    skipped: list[dict[str, str]] = []
    for binding in bindings:
        reason: str | None = None
        if binding.scope == "global":
            store = stores.get(("global", "org", binding.slug))
            if store is None:
                reason = "the memory does not exist"
            elif store.status != "active":
                reason = "the memory is archived"
            elif not reaches_project(store, bundle.slug):
                reason = f"the memory does not reach project {bundle.slug}"
        elif binding.slug == OWN and not agent_slug:
            reason = "a playbook run has no agent, so no memory of its own"
        elif binding.slug not in (SHARED, OWN):
            store = stores.get(("project", str(bundle.project.id), binding.slug))
            if store is None:
                reason = f"the project has no memory `{binding.slug}`"
            elif store.status != "active":
                reason = "the memory is archived"
        if reason is not None:
            skipped.append({"memory": binding.label, "reason": reason})
            continue
        namespace = namespace_of_binding(
            bundle.org_memory_key or bundle.org_slug, bundle.slug, agent_slug, binding
        )
        if namespace is None:  # unreachable: `own` without an agent is refused above
            continue
        resolved.append(
            StageMemory(
                label=binding.label,
                scope=binding.scope,
                slug=binding.slug,
                mode=binding.mode,
                namespace=namespace,
            )
        )
    return ResolvedMemories(resolved, skipped)
