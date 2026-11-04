# SPDX-License-Identifier: Apache-2.0
"""How a skill is shown and where it may be imported from, shared by the skill routers (ADR 0099)."""

from __future__ import annotations

from choregos_core.egress import EgressRefusedError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, AgentVersion, Connector, Organization, Project, Skill, SkillVersion
from ..errors import ApiError, forbidden, unprocessable, upstream
from ..rbac import Permission, Principal
from ..schemas import SkillDto, SkillFinding, SkillSourceDto, SkillVersionDto
from .skill_sources import (
    DEFAULT_SOURCES,
    SOURCE,
    GitSource,
    Snapshot,
    SourceFetcher,
    SourceRefused,
    SourceUnavailable,
    read_tarball,
)


def require_skill_reader(principal: Principal, org: str) -> None:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"reading the skills of {org} requires being a member of it")


async def skill_users(session: AsyncSession, organisation: Organization, slug: str) -> list[str]:
    rows = await session.execute(
        select(Agent.slug, AgentVersion.version, AgentVersion.spec)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        # A personal agent is its owner's business: it does not show among the skill's users (ADR 0061).
        .where(AgentVersion.org_id == organisation.id, Agent.scope != "personal")
        .order_by(Agent.slug, AgentVersion.version)
    )
    return [
        f"{agent}@{version}"
        for agent, version, spec in rows.tuples()
        if any(ref.get("slug") == slug for ref in (spec or {}).get("skills", []))
    ]


def skill_version_dto(version: SkillVersion, *, files: bool = False) -> SkillVersionDto:
    return SkillVersionDto(
        version=version.version,
        digest=version.digest,
        created_by=version.created_by,
        created_at=version.created_at,
        files=version.files if files else None,
        source=SkillSourceDto.model_validate(version.source) if version.source else None,
        risk=version.risk,
        review_status=version.review_status or "approved",
        findings=[SkillFinding.model_validate(f) for f in version.findings or []],
        reviewed_by=version.reviewed_by,
        reviewed_at=version.reviewed_at,
    )


async def skill_dto(
    session: AsyncSession, organisation: Organization, skill: Skill, *, with_versions: bool = False
) -> SkillDto:
    versions = list(
        (
            await session.execute(
                select(SkillVersion)
                .where(SkillVersion.skill_id == skill.id)
                .order_by(SkillVersion.version.desc())
            )
        ).scalars()
    )
    return SkillDto(
        slug=skill.slug,
        description=skill.description,
        status=skill.status,
        latest_version=versions[0].version if versions else 0,
        used_by=await skill_users(session, organisation, skill.slug),
        versions=[skill_version_dto(v) for v in versions] if with_versions else None,
        risk=versions[0].risk if versions else None,
        review_status=(versions[0].review_status if versions else None) or "approved",
    )


async def skill_sources(session: AsyncSession, organisation: Organization) -> tuple[list[str], bool]:
    """The organisation's allowlist, and whether it is the default one.

    By default: `github.com/anthropics/skills`, and the GitHub owner of each repository a project
    of the organisation is connected to — its own repositories."""
    written = (organisation.settings or {}).get("skill_sources")
    if isinstance(written, list):
        return [str(source) for source in written], False
    sources = list(DEFAULT_SOURCES)
    configs = await session.execute(
        select(Connector.config)
        .join(Project, Project.id == Connector.project_id)
        .where(Project.org_id == organisation.id, Connector.type.in_(("github", "github-issues")))
    )
    for (config,) in configs.tuples():
        repository = str((config or {}).get("repo") or "")
        owner = f"github.com/{repository.split('/', 1)[0]}" if "/" in repository else ""
        if owner and SOURCE.match(owner) and owner not in sources:
            sources.append(owner)
    return sources, True


def source_unavailable(source: GitSource, failure: SourceUnavailable) -> ApiError:
    """An egress refusal is the organisation's to fix (422, naming the host and who opens it), not
    an upstream failure (502)."""
    refusal = failure.__cause__
    if isinstance(refusal, EgressRefusedError):
        return unprocessable(str(refusal))
    return upstream(source.host, str(failure))


async def skill_snapshot(
    fetcher: SourceFetcher, source: GitSource, sha: str, path: str, agent: str = ""
) -> Snapshot:
    try:
        return read_tarball(await fetcher.tarball(source, sha, user_agent=agent), path)
    except SourceRefused as refused:
        raise unprocessable(str(refused)) from refused
    except SourceUnavailable as failure:
        raise source_unavailable(source, failure) from failure
