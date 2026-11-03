# SPDX-License-Identifier: Apache-2.0
"""The skill library of the organisation (ADR 0033).

A skill is a folder an agent carries; its versions never change. What goes in — by
JSON or by a zip archive — is checked by `services.skills`: a `name` header that is the skill's
own, a `description`, no declared permission, nothing leaving the folder.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from choregos_contracts import skill_file_bytes
from fastapi import APIRouter, Path, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..config import Settings
from ..db.models import Organization, Skill, SkillVersion
from ..deps import Config, Db, Me
from ..errors import conflict, forbidden, not_found, unprocessable
from ..rbac import Permission
from ..schemas import (
    SkillCandidate,
    SkillDto,
    SkillFiles,
    SkillFinding,
    SkillGitImport,
    SkillGitPreview,
    SkillGitPreviewRequest,
    SkillSourceDto,
    SkillSources,
    SkillUpdate,
    SkillVersionDto,
)
from ..services.egress import egress_scope, ensure_or_409, skill_import_targets
from ..services.organisations import organisation_by_slug
from ..services.platform_network import check_platform_host, user_agent
from ..services.skill_gate import assess
from ..services.skill_sources import (
    SHA,
    SOURCE,
    FoundSkill,
    GitSource,
    Snapshot,
    SourceFetcher,
    SourceRefused,
    SourceUnavailable,
    allowed,
    detect_licence,
    folder,
    get_fetcher,
    parse_repo_url,
)
from ..services.skill_views import (
    require_skill_reader,
    skill_dto,
    skill_snapshot,
    skill_sources,
    skill_version_dto,
    source_unavailable,
)
from ..services.skills import SkillRefusee, empreinte, publish_version, read_zip, valider

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
#: An archive bigger than this does not need to be opened to be refused.
ARCHIVE_MAX = 8 * 1024 * 1024


async def _skill(session: AsyncSession, organisation: Organization, slug: str) -> Skill:
    skill = (
        await session.execute(select(Skill).where(Skill.org_id == organisation.id, Skill.slug == slug))
    ).scalar_one_or_none()
    if skill is None:
        raise not_found("Skill", f"{organisation.slug}/{slug}")
    return skill


def _gerer(principal: Me, org: str) -> None:
    if not principal.can(Permission.AGENT_MANAGE, org):
        raise forbidden("publishing a skill needs the org_admin role")


@router.get("/orgs/{org}/skills", response_model=list[SkillDto], operation_id="listSkills")
async def list_skills(org: str, session: Db, principal: Me) -> list[SkillDto]:
    require_skill_reader(principal, org)
    organisation = await organisation_by_slug(session, org)
    skills = (
        await session.execute(select(Skill).where(Skill.org_id == organisation.id).order_by(Skill.slug))
    ).scalars()
    return [await skill_dto(session, organisation, skill) for skill in skills]


@router.post(
    "/orgs/{org}/skills",
    response_model=SkillDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSkill",
)
async def create_skill(org: str, body: SkillFiles, session: Db, principal: Me) -> SkillDto:
    """A new skill, version 1; its name is the one in its SKILL.md."""
    _gerer(principal, org)
    organisation = await organisation_by_slug(session, org)
    try:
        name = valider(body.files).name
    except SkillRefusee as refus:
        raise unprocessable(str(refus)) from refus
    if (
        await session.execute(select(Skill.id).where(Skill.org_id == organisation.id, Skill.slug == name))
    ).first():
        raise conflict(f"skill `{name}` already exists: publish its next version instead")
    skill, _ = await publish_version(session, principal, organisation, body.files, None)
    return await skill_dto(session, organisation, skill, with_versions=True)


@router.post(
    "/orgs/{org}/skills/import",
    response_model=SkillDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="importSkill",
)
async def import_skill(org: str, request: Request, session: Db, principal: Me) -> SkillDto:
    """A zip archive: the skill is born, or its next version is published."""
    _gerer(principal, org)
    organisation = await organisation_by_slug(session, org)
    content = await request.body()
    if len(content) > ARCHIVE_MAX:
        raise unprocessable(f"archive larger than {ARCHIVE_MAX // (1024 * 1024)} MiB")
    try:
        files, executables = read_zip(content)
    except SkillRefusee as refus:
        raise unprocessable(str(refus)) from refus
    skill, _ = await publish_version(session, principal, organisation, files, None, executables=executables)
    return await skill_dto(session, organisation, skill, with_versions=True)


@router.get("/orgs/{org}/skills/{slug}", response_model=SkillDto, operation_id="getSkill")
async def get_skill(org: str, slug: Slug, session: Db, principal: Me) -> SkillDto:
    require_skill_reader(principal, org)
    organisation = await organisation_by_slug(session, org)
    return await skill_dto(
        session, organisation, await _skill(session, organisation, slug), with_versions=True
    )


@router.post(
    "/orgs/{org}/skills/{slug}/versions",
    response_model=SkillVersionDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="publishSkillVersion",
)
async def publish_skill_version(
    org: str, slug: Slug, body: SkillFiles, session: Db, principal: Me
) -> SkillVersionDto:
    _gerer(principal, org)
    organisation = await organisation_by_slug(session, org)
    await _skill(session, organisation, slug)
    _, version = await publish_version(session, principal, organisation, body.files, slug)
    return skill_version_dto(version)


@router.get(
    "/orgs/{org}/skills/{slug}/versions/{version}",
    response_model=SkillVersionDto,
    operation_id="getSkillVersion",
)
async def get_skill_version(
    org: str, slug: Slug, version: int, session: Db, principal: Me
) -> SkillVersionDto:
    require_skill_reader(principal, org)
    skill = await _skill(session, await organisation_by_slug(session, org), slug)
    row = (
        await session.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == version)
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Skill version", f"{slug}@{version}")
    return skill_version_dto(row, files=True)


# ───────────────────────────── from a git repository (S32-05, ADR 0050) ─────────────────────────────


def _may_import(principal: Me, org: str, source: GitSource, permitted: bool, existing: list[str]) -> None:
    """An administrator imports from anywhere; a project owner from the allowlist, new skills only —
    a new version of an existing skill changes what the agents that carry it read."""
    if principal.can(Permission.AGENT_MANAGE, org):
        return
    if not principal.can(Permission.SKILL_IMPORT, org):
        raise forbidden("importing a skill needs the project_owner or org_admin role")
    if not permitted:
        raise forbidden(
            f"`{source.key}` is not in the organisation's allowlist of skill sources: "
            "an org_admin may import from it"
        )
    if existing:
        raise forbidden(
            f"`{', '.join(existing)}` already in the library: publishing its next version needs the "
            "org_admin role"
        )


async def _egress_gate(
    session: AsyncSession,
    principal: Me,
    organisation: Organization,
    source: GitSource,
    allow_egress: bool,
    settings: Settings,
) -> None:
    """The hosts a git import calls must be in the organisation's egress allowlist (S39-22): the
    modal's 409, or, for an administrator who says so, opened in this same transaction."""
    await ensure_or_409(
        session,
        principal,
        organisation,
        skill_import_targets(source.url),
        allow_egress=allow_egress,
        origin=f"skill-import:{source.key}",
        reason=f"importing skills from {source.key}",
        settings=settings,
    )


def _source(repo_url: str, path: str) -> tuple[GitSource, str]:
    try:
        return parse_repo_url(repo_url), folder(path)
    except SourceRefused as refus:
        raise unprocessable(str(refus)) from refus


async def _network_gate(session: AsyncSession, org_id: str, source: GitSource) -> str:
    """The organisation's network bounds apply to the fetch (ADR 0070): 422 when its ceiling refuses
    the repository's host, before the egress gate's 409. Returns the User-Agent the fetch carries."""
    await check_platform_host(session, org_id, source.url, "skill source")
    return await user_agent(session, org_id)


async def _resolve(fetcher: SourceFetcher, source: GitSource, ref: str, agent: str = "") -> str:
    try:
        return await fetcher.resolve(source, ref, user_agent=agent)
    except SourceRefused as refus:
        raise unprocessable(str(refus)) from refus
    except SourceUnavailable as outage:
        raise source_unavailable(source, outage) from outage


async def _latest_versions(
    session: AsyncSession, organisation: Organization, names: list[str]
) -> dict[str, int]:
    rows = await session.execute(
        select(Skill.slug, func.max(SkillVersion.version))
        .join(SkillVersion, SkillVersion.skill_id == Skill.id)
        .where(Skill.org_id == organisation.id, Skill.slug.in_(names))
        .group_by(Skill.slug)
    )
    return {slug: int(version) for slug, version in rows.tuples()}


def _licence(skill: FoundSkill, snapshot: Snapshot) -> str | None:
    return detect_licence(skill.files, snapshot.licences)


@router.get("/orgs/{org}/skill-sources", response_model=SkillSources, operation_id="getSkillSources")
async def get_skill_sources(org: str, session: Db, principal: Me) -> SkillSources:
    require_skill_reader(principal, org)
    sources, default = await skill_sources(session, await organisation_by_slug(session, org))
    return SkillSources(sources=sources, default=default)


@router.put("/orgs/{org}/skill-sources", response_model=SkillSources, operation_id="putSkillSources")
async def put_skill_sources(org: str, body: SkillSources, session: Db, principal: Me) -> SkillSources:
    """The allowlist, written by the administrator; it replaces the default one."""
    if not principal.can(Permission.AGENT_MANAGE, org):
        raise forbidden("the skill sources of an organisation are decided by its administrator")
    organisation = await organisation_by_slug(session, org)
    malformed = [source for source in body.sources if not SOURCE.match(source)]
    if malformed:
        raise unprocessable(
            f"not a source: {', '.join(malformed)} — expected `github.com/<owner>[/<repo>]` "
            "or `gitlab.com/<group>[/…]`"
        )
    sources = list(dict.fromkeys(body.sources))
    organisation.settings = {**(organisation.settings or {}), "skill_sources": sources}
    await record(session, principal, "skill.sources", org_id=organisation.id, sources=sources)
    return SkillSources(sources=sources, default=False)


@router.post(
    "/orgs/{org}/skills/import-git/preview",
    response_model=SkillGitPreview,
    operation_id="previewSkillsFromGit",
)
async def preview_skills_from_git(
    org: str, body: SkillGitPreviewRequest, session: Db, principal: Me, settings: Config
) -> SkillGitPreview:
    """The skills a repository holds at a reference, resolved to its commit SHA. Nothing is written:
    the import then names that SHA and the skills a person picked."""
    organisation = await organisation_by_slug(session, org)
    source, base = _source(body.repo_url, body.path)
    sources, _ = await skill_sources(session, organisation)
    permitted = allowed(source, sources)
    _may_import(principal, org, source, permitted, [])
    agent = await _network_gate(session, organisation.id, source)
    await _egress_gate(session, principal, organisation, source, body.allow_egress, settings)
    fetcher = get_fetcher(settings.fakes)
    async with egress_scope(session, organisation, "api", f"skill-import:{source.key}", settings):
        sha = await _resolve(fetcher, source, body.ref, agent)
        snapshot = await skill_snapshot(fetcher, source, sha, base, agent)
    existing = await _latest_versions(
        session, organisation, [skill.name for skill in snapshot.skills if skill.name]
    )
    gate_results = {
        s.path: assess(s.files, executables=s.executables, repo_licences=snapshot.licences)
        for s in snapshot.skills
    }
    return SkillGitPreview(
        repo=source.url,
        ref=body.ref,
        sha=sha,
        path=base,
        allowed=permitted,
        skills=[
            SkillCandidate(
                path=skill.path,
                name=skill.name,
                description=skill.description,
                files=len(skill.files),
                bytes=sum(
                    len(skill_file_bytes(skill_path, text)) for skill_path, text in skill.files.items()
                ),
                licence=_licence(skill, snapshot),
                problem=skill.problem,
                existing_version=existing.get(skill.name or ""),
                risk=None if skill.problem else gate_results[skill.path].risk,
                findings=[]
                if skill.problem
                else [SkillFinding.model_validate(f.as_dict()) for f in gate_results[skill.path].findings],
            )
            for skill in snapshot.skills
        ],
    )


@router.post(
    "/orgs/{org}/skills/import-git",
    response_model=list[SkillDto],
    status_code=status.HTTP_201_CREATED,
    operation_id="importSkillsFromGit",
)
async def import_skills_from_git(
    org: str, body: SkillGitImport, session: Db, principal: Me, settings: Config
) -> list[SkillDto]:
    """The skills a person picked, at a commit SHA: each becomes a new skill, or its next version,
    recording its repository, SHA, folder and licence."""
    if not SHA.match(body.sha):
        raise unprocessable(
            f"`{body.sha}` is not a commit SHA: a branch or a tag can move under an import — pin the "
            "40-character SHA the preview resolved"
        )
    organisation = await organisation_by_slug(session, org)
    source, base = _source(body.repo_url, body.path)
    sources, _ = await skill_sources(session, organisation)
    names = list(dict.fromkeys(body.skills))
    existing = await _latest_versions(session, organisation, names)
    _may_import(principal, org, source, allowed(source, sources), sorted(existing))
    agent = await _network_gate(session, organisation.id, source)
    await _egress_gate(session, principal, organisation, source, body.allow_egress, settings)
    async with egress_scope(session, organisation, "api", f"skill-import:{source.key}", settings):
        snapshot = await skill_snapshot(get_fetcher(settings.fakes), source, body.sha, base, agent)
    chosen: list[FoundSkill] = []
    for name in names:
        matches = [skill for skill in snapshot.skills if skill.name == name]
        if not matches:
            raise unprocessable(f"no skill named `{name}` under `{base or '/'}` at {body.sha[:12]}")
        if len(matches) > 1:
            folder_list = ", ".join(skill.path for skill in matches)
            raise unprocessable(f"`{name}` names {len(matches)} folders ({folder_list}): narrow the path")
        if matches[0].problem:
            raise unprocessable(f"`{name}` ({matches[0].path}): {matches[0].problem}")
        chosen.append(matches[0])
    published: list[Skill] = []
    for skill in chosen:
        provenance: dict[str, object] = {
            "repo": source.url,
            "sha": body.sha,
            "path": skill.path,
            "licence": _licence(skill, snapshot),
        }
        published_skill, _ = await publish_version(
            session,
            principal,
            organisation,
            skill.files,
            None,
            provenance,
            executables=skill.executables,
            repo_licences=snapshot.licences,
        )
        published.append(published_skill)
    return [await skill_dto(session, organisation, skill, with_versions=True) for skill in published]


@router.get("/orgs/{org}/skills/{slug}/update", response_model=SkillUpdate, operation_id="checkSkillUpdate")
async def check_skill_update(
    org: str,
    slug: Slug,
    session: Db,
    principal: Me,
    settings: Config,
    ref: Annotated[str, Query(min_length=1, max_length=200)] = "HEAD",
) -> SkillUpdate:
    """What the skill's repository holds now at `ref` (its default branch by default), against its
    latest version. A proposal, never applied: importing the SHA it gives publishes it."""
    if not (principal.can(Permission.SKILL_IMPORT, org) or principal.can(Permission.AGENT_MANAGE, org)):
        raise forbidden("checking a skill's repository needs the project_owner or org_admin role")
    organisation = await organisation_by_slug(session, org)
    skill = await _skill(session, organisation, slug)
    latest = (
        await session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill.id)
            .order_by(SkillVersion.version.desc())
            .limit(1)
        )
    ).scalar_one()
    if not latest.source:
        raise unprocessable(
            f"`{slug}` v{latest.version} was not imported from git: there is nothing to check"
        )
    origin = SkillSourceDto.model_validate(latest.source)
    source, skill_path = _source(origin.repo, origin.path)
    agent = await _network_gate(session, organisation.id, source)
    fetcher = get_fetcher(settings.fakes)
    async with egress_scope(session, organisation, "api", f"skill-import:{source.key}", settings):
        sha = await _resolve(fetcher, source, ref, agent)
    response = SkillUpdate(
        slug=slug, version=latest.version, source=origin, ref=ref, sha=sha, up_to_date=sha == origin.sha
    )
    if response.up_to_date:
        response.digest = latest.digest
        return response
    async with egress_scope(session, organisation, "api", f"skill-import:{source.key}", settings):
        snapshot = await skill_snapshot(fetcher, source, sha, skill_path, agent)
    found = next((s for s in snapshot.skills if s.path == skill_path), None)
    if found is None:
        response.problem = f"there is no skill at `{skill_path or '/'}` any more at {sha[:12]}"
    elif found.problem:
        response.problem = found.problem
    elif found.name != slug:
        response.problem = f"the skill at `{skill_path or '/'}` is now named `{found.name}`"
    else:
        before: dict[str, str] = dict(latest.files or {})
        after = found.files
        response.digest = empreinte(after)
        response.added = sorted(set(after) - set(before))
        response.removed = sorted(set(before) - set(after))
        response.modified = sorted(c for c in set(before) & set(after) if before[c] != after[c])
    return response


# ───────────────────────────── the second reviewer (S32-06, ADR 0050) ─────────────────────────────


@router.post(
    "/orgs/{org}/skills/{slug}/versions/{version}/approve",
    response_model=SkillVersionDto,
    operation_id="approveSkillVersion",
)
async def approve_skill_version(
    org: str, slug: Slug, version: int, session: Db, principal: Me
) -> SkillVersionDto:
    """A high-risk version becomes usable: an administrator who is NOT its importer read it."""
    if not principal.can(Permission.AGENT_MANAGE, org):
        raise forbidden("approving a skill version needs the org_admin role")
    organisation = await organisation_by_slug(session, org)
    skill = await _skill(session, organisation, slug)
    row = (
        await session.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == version)
        )
    ).scalar_one_or_none()
    if row is None:
        raise not_found("Skill version", f"{slug}@{version}")
    if row.review_status != "pending_review":
        raise conflict(f"`{slug}` v{version} is not waiting for a review")
    if row.created_by and row.created_by.lower() == principal.email.lower():
        raise forbidden(
            f"`{slug}` v{version} was imported by you: a second person approves a high-risk version"
        )
    row.review_status = "approved"
    row.reviewed_by = principal.email
    row.reviewed_at = datetime.now(UTC)
    await session.flush()
    await record(
        session,
        principal,
        "skill.approve",
        org_id=organisation.id,
        target_type="skill",
        target_id=skill.id,
        version=version,
        risk=row.risk,
        imported_by=row.created_by,
    )
    return skill_version_dto(row, files=True)
