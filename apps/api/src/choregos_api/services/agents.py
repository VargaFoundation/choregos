# SPDX-License-Identifier: Apache-2.0
"""The agent registry, service side (ADR 0033): what a project pin may change in a version, the
effective version, and the resolution of a `slug[@version]` reference for a run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, time

from choregos_core import utcnow
from choregos_core.compat import alias_names
from choregos_core.hosted import native_sidecars_refusal
from choregos_core.instructions import instruction_errors
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Agent, AgentVersion, Project, ProjectAgent
from ..deps import _aware
from ..errors import unprocessable
from ..schemas import AgentOverrides, AgentSpec, CodeRuntime, HostedImageSource, HostedRuntime
from .agent_governance import usable_in_project
from .agent_releases import served


def digest(spec: AgentSpec) -> str:
    """The digest of a version: what a run records, and what is compared."""
    content = spec.model_dump(mode="json")
    # Added after versions were published (ADR 0051): absent, it leaves their digest unchanged.
    if not content.get("knowledge"):
        content.pop("knowledge", None)
    # The default memory scope (S32-02) is left out: a version published before the field existed
    # keeps its digest, and the catalogue does not see every agent as changed.
    if content.get("memory") == "project":
        del content["memory"]
    # Same for the runtime (ADR 0063): a version that names none keeps the digest it had.
    if content.get("runtime") is None:
        content.pop("runtime", None)
    # `memories` (S40-11) needs nothing here: `AgentSpec` does not serialise it while unset, so every
    # version published before it keeps its digest.
    # The default network (ADR 0070): every field unset, as in a version published before it existed.
    if not any(value is not None for value in (content.get("network") or {}).values()):
        content.pop("network", None)
    blob = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def version_errors(spec: AgentSpec) -> None:
    """Instructions that would not render (syntax, unknown variable, or access refused by the
    sandbox) are refused at publication, not discovered at the first run."""
    errors = instruction_errors(spec.instructions)
    if errors:
        raise unprocessable(
            "the agent's instructions do not render",
            [{"loc": ["instructions"], "msg": e} for e in errors],
        )
    if isinstance(spec.runtime, CodeRuntime):
        # The image is a NAME the operator mapped (ADR 0063): an unknown one would fail at the first
        # run, so it is refused when the version is published, naming the ones that exist.
        known = sorted(get_settings().agent_images)
        if spec.runtime.image not in known:
            raise unprocessable(
                "the agent's image is not one the operator named",
                [
                    {
                        "loc": ["runtime", "image"],
                        "msg": f"unknown image `{spec.runtime.image}` "
                        f"(CHOREGOS_AGENT_IMAGES knows: {', '.join(known) or 'none'})",
                    }
                ],
            )
    if isinstance(spec.runtime, HostedRuntime):
        cluster = get_settings().cluster_version
        refusal = native_sidecars_refusal(cluster) if cluster else None
        if refusal is not None:
            # The executor refuses the start too, from the API server itself; this tells the author
            # now rather than at the first run.
            raise unprocessable(
                "this cluster cannot run a hosted agent",
                [{"loc": ["runtime"], "msg": f"{refusal} (upgrade the cluster to publish this version)"}],
            )
        if isinstance(spec.runtime.source, HostedImageSource):
            # ADR 0080 §8: under the operator's allowlist; the digest is resolved where the version
            # is written (`pin_hosted_runtime`), never taken from the caller.
            from .hosted_images import allowlisted_ref

            allowlisted_ref(spec.runtime.source)
        else:
            # ADR 0080 §9: the zip itself is read where the version is written (`pin_hosted_runtime`).
            from .hosted_sources import SourceRefused, base_image

            try:
                base_image(spec.runtime.source.runtime)
            except SourceRefused as exc:
                raise unprocessable(
                    "the agent's hosted source cannot be built",
                    [{"loc": ["runtime", "source", "runtime"], "msg": str(exc)}],
                ) from exc


def _tools(spec: AgentSpec) -> set[str]:
    return {tool for server in spec.mcp_servers for tool in server.tools}


def widenings(spec: AgentSpec, overrides: AgentOverrides) -> list[str]:
    """What an override WOULD WIDEN: a project only ever tightens a version."""
    gaps: list[str] = []
    for group in ("limits", "budget"):
        of_version = getattr(spec, group)
        of_project = getattr(overrides, group)
        for field in type(of_version).model_fields:
            ceiling, asked = getattr(of_version, field), getattr(of_project, field)
            if asked is not None and ceiling is not None and asked > ceiling:
                gaps.append(f"{group}.{field}: {asked} exceeds the version's {ceiling}")
    if overrides.tools is not None:
        extra = sorted(set(overrides.tools) - _tools(spec))
        if extra:
            gaps.append(f"tools the version does not grant: {', '.join(extra)}")
    return gaps


def effective(spec: AgentSpec, overrides: AgentOverrides) -> AgentSpec:
    """The version with its overrides applied: what will run."""
    result = spec.model_copy(deep=True)
    for group in ("limits", "budget"):
        target = getattr(result, group)
        for field in type(target).model_fields:
            asked = getattr(getattr(overrides, group), field)
            if asked is None:
                continue
            # The minimum, never an assignment: a pin that follows a channel meets revisions its
            # override was not validated against (S40-02), and a looser one must not widen them.
            ceiling = getattr(target, field)
            setattr(target, field, asked if ceiling is None else min(ceiling, asked))
    if overrides.tools is not None:
        kept = set(overrides.tools)
        for server in result.mcp_servers:
            server.tools = [tool for tool in server.tools if tool in kept]
    return result


class AgentUnavailable(RuntimeError):  # noqa: N818 - a refusal with its reason, told to the run
    """The agent an actor names does not exist, is not active, or has expired: the run does not start.

    The Temporal error type recorded for it stays `AgentIndisponible` (S45-39)."""


@dataclass(frozen=True)
class ResolvedAgent:
    agent: Agent
    version: int
    spec: AgentSpec
    #: The version comes from the project's pin (fixed or by channel), not from the reference nor from
    #: the agent's channels.
    pinned: bool = False
    #: Why this revision (S40-02): `explicit`, `pin`, `pin_channel`, `project_channel`, `canary`,
    #: `stable` (production) or `latest` (an agent with no production pointer).
    source: str = "stable"
    #: The channel the revision was read from, when it was one.
    channel: str | None = None


async def resolve_agent(
    session: AsyncSession,
    org_id: str,
    project_id: str,
    reference: str,
    *,
    work_item_id: str | None = None,
) -> ResolvedAgent:
    """The revision a run loads, first match wins (ADR 0071): `slug@N`; the project's fixed pin; its
    pin on a channel; the project's "follow testing" switch; the deterministic canary share of the
    work item; production. The project's overrides apply in every case: they only tighten."""
    slug, _, requested = reference.partition("@")
    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None:
        raise AgentUnavailable(f"agent `{slug}` does not exist in the project's organisation")
    # A personal agent never runs in a workflow, a project's agent only in its project (ADR 0061).
    refusal = usable_in_project(agent.scope, agent.project_id, agent.slug, project_id)
    if refusal is not None:
        raise AgentUnavailable(refusal)
    if agent.status != "active":
        raise AgentUnavailable(f"agent `{slug}` is {agent.status}: the run does not start")
    if agent.lifecycle == "retired":
        raise AgentUnavailable(f"agent `{slug}` is retired: the run does not start")
    if agent.lifecycle == "draft":
        raise AgentUnavailable(f"agent `{slug}` is a draft: activate it before a run uses it")
    if agent.expires_at is not None and _aware(agent.expires_at) <= utcnow():
        raise AgentUnavailable(f"agent `{slug}` expired on {agent.expires_at:%Y-%m-%d}")
    pin = (
        await session.execute(
            select(ProjectAgent).where(
                ProjectAgent.project_id == project_id, ProjectAgent.agent_id == agent.id
            )
        )
    ).scalar_one_or_none()
    channel: str | None
    if requested:
        version: int | None = int(requested)
        source, channel = "explicit", None
    else:
        project = await session.get(Project, project_id)
        version, source, channel = served(
            agent,
            pin=pin,
            project_channel=(project.agent_channel if project is not None else "stable") or "stable",
            work_item_id=work_item_id,
        )
    if version is None:
        version = (
            await session.execute(
                select(AgentVersion.version)
                .where(AgentVersion.agent_id == agent.id)
                .order_by(AgentVersion.version.desc())
                .limit(1)
            )
        ).scalar_one()
    row = (
        await session.execute(
            select(AgentVersion).where(AgentVersion.agent_id == agent.id, AgentVersion.version == version)
        )
    ).scalar_one_or_none()
    if row is None:
        raise AgentUnavailable(f"agent `{slug}` has no version {version}")
    spec = AgentSpec.model_validate(row.spec)
    overrides = AgentOverrides.model_validate(pin.overrides or {}) if pin is not None else AgentOverrides()
    return ResolvedAgent(
        agent=agent,
        version=version,
        spec=effective(spec, overrides),
        pinned=source in {"pin", "pin_channel"},
        source=source,
        channel=channel,
    )


async def spend_since(session: AsyncSession, org_id: str, agent_slug: str, since: datetime) -> float:
    """What the agent's runs cost since `since`, models AND tools."""
    from sqlalchemy import func

    from ..db.models import CostLedger, Project, Run

    total = (
        await session.execute(
            select(func.coalesce(func.sum(CostLedger.cost_usd), 0.0))
            .join(Run, Run.id == CostLedger.run_id)
            .join(Project, Project.id == Run.project_id)
            .where(Run.agent_slug == agent_slug, Project.org_id == org_id, CostLedger.ts >= since)
        )
    ).scalar_one()
    return float(total)


async def spend_today(session: AsyncSession, org_id: str, agent_slug: str) -> float:
    """What the agent's runs have cost since midnight (UTC), models AND tools."""
    midnight = datetime.combine(utcnow().date(), time.min, tzinfo=utcnow().tzinfo)
    return await spend_since(session, org_id, agent_slug, midnight)


async def spend_this_month(session: AsyncSession, org_id: str, agent_slug: str) -> float:
    """What the agent's runs have cost since the first of the month (UTC), models AND tools."""
    first = datetime.combine(utcnow().date().replace(day=1), time.min, tzinfo=utcnow().tzinfo)
    return await spend_since(session, org_id, agent_slug, first)


empreinte = digest  # lang: fr-ok alias:internal
erreurs_d_une_version = version_errors  # lang: fr-ok alias:internal
elargissements = widenings  # lang: fr-ok alias:internal
AgentIndisponible = AgentUnavailable  # lang: fr-ok alias:internal
AgentResolu = ResolvedAgent  # lang: fr-ok alias:internal
resoudre_l_agent = resolve_agent  # lang: fr-ok alias:internal
depense_depuis = spend_since  # lang: fr-ok alias:internal
depense_du_jour = spend_today  # lang: fr-ok alias:internal
depense_du_mois = spend_this_month  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "empreinte": "digest",  # lang: fr-ok alias:internal
        "erreurs_d_une_version": "version_errors",  # lang: fr-ok alias:internal
        "elargissements": "widenings",  # lang: fr-ok alias:internal
        "AgentIndisponible": "AgentUnavailable",  # lang: fr-ok alias:internal
        "AgentResolu": "ResolvedAgent",  # lang: fr-ok alias:internal
        "resoudre_l_agent": "resolve_agent",  # lang: fr-ok alias:internal
        "depense_depuis": "spend_since",  # lang: fr-ok alias:internal
        "depense_du_jour": "spend_today",  # lang: fr-ok alias:internal
        "depense_du_mois": "spend_this_month",  # lang: fr-ok alias:internal
    },
)

__all__ = [
    "AgentUnavailable",
    "ResolvedAgent",
    "digest",
    "effective",
    "resolve_agent",
    "spend_since",
    "spend_this_month",
    "spend_today",
    "version_errors",
    "widenings",
]
