# SPDX-License-Identifier: Apache-2.0
"""La bibliothèque de skills de l'organisation (ADR 0033).

Une skill est un dossier qu'un agent porte ; ses versions ne se modifient jamais. Ce qui entre — par
JSON ou par une archive zip — est vérifié par `services.skills` : un en-tête `name` qui est celui de
la skill, une `description`, aucune permission déclarée, aucune sortie du dossier.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentVersion, Connector, Organization, Project, Skill, SkillVersion
from ..deps import Config, Db, Me
from ..errors import conflict, forbidden, not_found, unprocessable, upstream
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
from ..services.skill_gate import assess
from ..services.skill_gate import columns as gate_columns
from ..services.skill_sources import (
    DEFAULT_SOURCES,
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
    read_tarball,
)
from ..services.skills import SkillRefusee, empreinte, read_zip, valider

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
#: Une archive plus grosse que ça n'a pas besoin d'être ouverte pour être refusée.
ARCHIVE_MAX = 8 * 1024 * 1024


async def _organisation(session: AsyncSession, slug: str) -> Organization:
    organisation = (
        await session.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if organisation is None:
        raise not_found("Organisation", slug)
    return organisation


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


def _lire(principal: Me, org: str) -> None:
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden(f"reading the skills of {org} requires being a member of it")


async def _utilisee_par(session: AsyncSession, organisation: Organization, slug: str) -> list[str]:
    lignes = await session.execute(
        select(Agent.slug, AgentVersion.version, AgentVersion.spec)
        .join(Agent, Agent.id == AgentVersion.agent_id)
        .where(AgentVersion.org_id == organisation.id)
        .order_by(Agent.slug, AgentVersion.version)
    )
    return [
        f"{agent}@{version}"
        for agent, version, spec in lignes.tuples()
        if any(ref.get("slug") == slug for ref in (spec or {}).get("skills", []))
    ]


def _version_dto(version: SkillVersion, *, files: bool = False) -> SkillVersionDto:
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


async def _dto(
    session: AsyncSession, organisation: Organization, skill: Skill, *, avec_versions: bool = False
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
        used_by=await _utilisee_par(session, organisation, skill.slug),
        versions=[_version_dto(v) for v in versions] if avec_versions else None,
        risk=versions[0].risk if versions else None,
        review_status=(versions[0].review_status if versions else None) or "approved",
    )


async def _publier(
    session: AsyncSession,
    principal: Me,
    organisation: Organization,
    fichiers: dict[str, str],
    slug: str | None,
    source: dict[str, object] | None = None,
    *,
    executables: list[str] | None = None,
    repo_licences: dict[str, str] | None = None,
) -> tuple[Skill, SkillVersion]:
    """Crée la skill (version 1) ou publie sa version suivante — jamais de réécriture. La version
    passe la porte (S32-06) : son risque, ce que le scan trouve ; `high` attend un second regard."""
    try:
        tete = valider(fichiers, slug)
    except SkillRefusee as refus:
        raise unprocessable(str(refus)) from refus
    skill = (
        await session.execute(select(Skill).where(Skill.org_id == organisation.id, Skill.slug == tete.name))
    ).scalar_one_or_none()
    if skill is None:
        skill = Skill(org_id=organisation.id, slug=tete.name, description=tete.description, status="active")
        session.add(skill)
        await session.flush()
    skill.description = tete.description
    derniere = (
        await session.execute(select(func.max(SkillVersion.version)).where(SkillVersion.skill_id == skill.id))
    ).scalar() or 0
    version = SkillVersion(
        skill_id=skill.id,
        org_id=organisation.id,
        version=derniere + 1,
        files=dict(fichiers),
        digest=empreinte(fichiers),
        created_by=principal.email,
        source=source,
        **gate_columns(assess(fichiers, executables=executables or [], repo_licences=repo_licences)),
    )
    session.add(version)
    await session.flush()
    await record(
        session,
        principal,
        "skill.publish",
        org_id=organisation.id,
        target_type="skill",
        target_id=skill.id,
        version=version.version,
        risk=version.risk,
        review_status=version.review_status,
        **({"source": source} if source else {}),
    )
    return skill, version


@router.get("/orgs/{org}/skills", response_model=list[SkillDto], operation_id="listSkills")
async def list_skills(org: str, session: Db, principal: Me) -> list[SkillDto]:
    _lire(principal, org)
    organisation = await _organisation(session, org)
    skills = (
        await session.execute(select(Skill).where(Skill.org_id == organisation.id).order_by(Skill.slug))
    ).scalars()
    return [await _dto(session, organisation, skill) for skill in skills]


@router.post(
    "/orgs/{org}/skills",
    response_model=SkillDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSkill",
)
async def create_skill(org: str, body: SkillFiles, session: Db, principal: Me) -> SkillDto:
    """Une skill neuve, version 1 ; son nom est celui de son SKILL.md."""
    _gerer(principal, org)
    organisation = await _organisation(session, org)
    try:
        nom = valider(body.files).name
    except SkillRefusee as refus:
        raise unprocessable(str(refus)) from refus
    if (
        await session.execute(select(Skill.id).where(Skill.org_id == organisation.id, Skill.slug == nom))
    ).first():
        raise conflict(f"skill `{nom}` already exists: publish its next version instead")
    skill, _ = await _publier(session, principal, organisation, body.files, None)
    return await _dto(session, organisation, skill, avec_versions=True)


@router.post(
    "/orgs/{org}/skills/import",
    response_model=SkillDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="importSkill",
)
async def import_skill(org: str, request: Request, session: Db, principal: Me) -> SkillDto:
    """Une archive zip : la skill naît, ou sa version suivante est publiée."""
    _gerer(principal, org)
    organisation = await _organisation(session, org)
    contenu = await request.body()
    if len(contenu) > ARCHIVE_MAX:
        raise unprocessable(f"archive larger than {ARCHIVE_MAX // (1024 * 1024)} MiB")
    try:
        fichiers, executables = read_zip(contenu)
    except SkillRefusee as refus:
        raise unprocessable(str(refus)) from refus
    skill, _ = await _publier(session, principal, organisation, fichiers, None, executables=executables)
    return await _dto(session, organisation, skill, avec_versions=True)


@router.get("/orgs/{org}/skills/{slug}", response_model=SkillDto, operation_id="getSkill")
async def get_skill(org: str, slug: Slug, session: Db, principal: Me) -> SkillDto:
    _lire(principal, org)
    organisation = await _organisation(session, org)
    return await _dto(session, organisation, await _skill(session, organisation, slug), avec_versions=True)


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
    organisation = await _organisation(session, org)
    await _skill(session, organisation, slug)
    _, version = await _publier(session, principal, organisation, body.files, slug)
    return _version_dto(version)


@router.get(
    "/orgs/{org}/skills/{slug}/versions/{version}",
    response_model=SkillVersionDto,
    operation_id="getSkillVersion",
)
async def get_skill_version(
    org: str, slug: Slug, version: int, session: Db, principal: Me
) -> SkillVersionDto:
    _lire(principal, org)
    skill = await _skill(session, await _organisation(session, org), slug)
    ligne = (
        await session.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == version)
        )
    ).scalar_one_or_none()
    if ligne is None:
        raise not_found("Skill version", f"{slug}@{version}")
    return _version_dto(ligne, files=True)


# ───────────────────────────── from a git repository (S32-05, ADR 0050) ─────────────────────────────


async def _skill_sources(session: AsyncSession, organisation: Organization) -> tuple[list[str], bool]:
    """The organisation's allowlist, and whether it is the default one.

    By default: `github.com/anthropics/skills`, and the GitHub owner of each repository a project
    of the organisation is connected to — its own repositories."""
    ecrites = (organisation.settings or {}).get("skill_sources")
    if isinstance(ecrites, list):
        return [str(source) for source in ecrites], False
    sources = list(DEFAULT_SOURCES)
    configs = await session.execute(
        select(Connector.config)
        .join(Project, Project.id == Connector.project_id)
        .where(Project.org_id == organisation.id, Connector.type.in_(("github", "github-issues")))
    )
    for (config,) in configs.tuples():
        depot = str((config or {}).get("repo") or "")
        proprietaire = f"github.com/{depot.split('/', 1)[0]}" if "/" in depot else ""
        if proprietaire and SOURCE.match(proprietaire) and proprietaire not in sources:
            sources.append(proprietaire)
    return sources, True


def _may_import(principal: Me, org: str, source: GitSource, permis: bool, existantes: list[str]) -> None:
    """An administrator imports from anywhere; a project owner from the allowlist, new skills only —
    a new version of an existing skill changes what the agents that carry it read."""
    if principal.can(Permission.AGENT_MANAGE, org):
        return
    if not principal.can(Permission.SKILL_IMPORT, org):
        raise forbidden("importing a skill needs the project_owner or org_admin role")
    if not permis:
        raise forbidden(
            f"`{source.key}` is not in the organisation's allowlist of skill sources: "
            "an org_admin may import from it"
        )
    if existantes:
        raise forbidden(
            f"`{', '.join(existantes)}` already in the library: publishing its next version needs the "
            "org_admin role"
        )


def _source(repo_url: str, path: str) -> tuple[GitSource, str]:
    try:
        return parse_repo_url(repo_url), folder(path)
    except SourceRefused as refus:
        raise unprocessable(str(refus)) from refus


async def _resolve(fetcher: SourceFetcher, source: GitSource, ref: str) -> str:
    try:
        return await fetcher.resolve(source, ref)
    except SourceRefused as refus:
        raise unprocessable(str(refus)) from refus
    except SourceUnavailable as panne:
        raise upstream(source.host, str(panne)) from panne


async def _snapshot(fetcher: SourceFetcher, source: GitSource, sha: str, path: str) -> Snapshot:
    try:
        return read_tarball(await fetcher.tarball(source, sha), path)
    except SourceRefused as refus:
        raise unprocessable(str(refus)) from refus
    except SourceUnavailable as panne:
        raise upstream(source.host, str(panne)) from panne


async def _latest_versions(
    session: AsyncSession, organisation: Organization, noms: list[str]
) -> dict[str, int]:
    lignes = await session.execute(
        select(Skill.slug, func.max(SkillVersion.version))
        .join(SkillVersion, SkillVersion.skill_id == Skill.id)
        .where(Skill.org_id == organisation.id, Skill.slug.in_(noms))
        .group_by(Skill.slug)
    )
    return {slug: int(version) for slug, version in lignes.tuples()}


def _licence(skill: FoundSkill, snapshot: Snapshot) -> str | None:
    return detect_licence(skill.files, snapshot.licences)


@router.get("/orgs/{org}/skill-sources", response_model=SkillSources, operation_id="getSkillSources")
async def get_skill_sources(org: str, session: Db, principal: Me) -> SkillSources:
    _lire(principal, org)
    sources, defaut = await _skill_sources(session, await _organisation(session, org))
    return SkillSources(sources=sources, default=defaut)


@router.put("/orgs/{org}/skill-sources", response_model=SkillSources, operation_id="putSkillSources")
async def put_skill_sources(org: str, body: SkillSources, session: Db, principal: Me) -> SkillSources:
    """The allowlist, written by the administrator; it replaces the default one."""
    if not principal.can(Permission.AGENT_MANAGE, org):
        raise forbidden("the skill sources of an organisation are decided by its administrator")
    organisation = await _organisation(session, org)
    mauvaises = [source for source in body.sources if not SOURCE.match(source)]
    if mauvaises:
        raise unprocessable(
            f"not a source: {', '.join(mauvaises)} — expected `github.com/<owner>[/<repo>]` "
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
    organisation = await _organisation(session, org)
    source, base = _source(body.repo_url, body.path)
    sources, _ = await _skill_sources(session, organisation)
    permis = allowed(source, sources)
    _may_import(principal, org, source, permis, [])
    fetcher = get_fetcher(settings.fakes)
    sha = await _resolve(fetcher, source, body.ref)
    snapshot = await _snapshot(fetcher, source, sha, base)
    existantes = await _latest_versions(
        session, organisation, [skill.name for skill in snapshot.skills if skill.name]
    )
    portes = {
        s.path: assess(s.files, executables=s.executables, repo_licences=snapshot.licences)
        for s in snapshot.skills
    }
    return SkillGitPreview(
        repo=source.url,
        ref=body.ref,
        sha=sha,
        path=base,
        allowed=permis,
        skills=[
            SkillCandidate(
                path=skill.path,
                name=skill.name,
                description=skill.description,
                files=len(skill.files),
                bytes=sum(len(texte.encode("utf-8")) for texte in skill.files.values()),
                licence=_licence(skill, snapshot),
                problem=skill.problem,
                existing_version=existantes.get(skill.name or ""),
                risk=None if skill.problem else portes[skill.path].risk,
                findings=[]
                if skill.problem
                else [SkillFinding.model_validate(f.as_dict()) for f in portes[skill.path].findings],
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
    organisation = await _organisation(session, org)
    source, base = _source(body.repo_url, body.path)
    sources, _ = await _skill_sources(session, organisation)
    noms = list(dict.fromkeys(body.skills))
    existantes = await _latest_versions(session, organisation, noms)
    _may_import(principal, org, source, allowed(source, sources), sorted(existantes))
    snapshot = await _snapshot(get_fetcher(settings.fakes), source, body.sha, base)
    choisies: list[FoundSkill] = []
    for nom in noms:
        trouvees = [skill for skill in snapshot.skills if skill.name == nom]
        if not trouvees:
            raise unprocessable(f"no skill named `{nom}` under `{base or '/'}` at {body.sha[:12]}")
        if len(trouvees) > 1:
            dossiers = ", ".join(skill.path for skill in trouvees)
            raise unprocessable(f"`{nom}` names {len(trouvees)} folders ({dossiers}): narrow the path")
        if trouvees[0].problem:
            raise unprocessable(f"`{nom}` ({trouvees[0].path}): {trouvees[0].problem}")
        choisies.append(trouvees[0])
    publiees: list[Skill] = []
    for skill in choisies:
        provenance: dict[str, object] = {
            "repo": source.url,
            "sha": body.sha,
            "path": skill.path,
            "licence": _licence(skill, snapshot),
        }
        publiee, _ = await _publier(
            session,
            principal,
            organisation,
            skill.files,
            None,
            provenance,
            executables=skill.executables,
            repo_licences=snapshot.licences,
        )
        publiees.append(publiee)
    return [await _dto(session, organisation, skill, avec_versions=True) for skill in publiees]


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
    organisation = await _organisation(session, org)
    skill = await _skill(session, organisation, slug)
    derniere = (
        await session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill.id)
            .order_by(SkillVersion.version.desc())
            .limit(1)
        )
    ).scalar_one()
    if not derniere.source:
        raise unprocessable(
            f"`{slug}` v{derniere.version} was not imported from git: there is nothing to check"
        )
    origine = SkillSourceDto.model_validate(derniere.source)
    source, chemin = _source(origine.repo, origine.path)
    fetcher = get_fetcher(settings.fakes)
    sha = await _resolve(fetcher, source, ref)
    reponse = SkillUpdate(
        slug=slug, version=derniere.version, source=origine, ref=ref, sha=sha, up_to_date=sha == origine.sha
    )
    if reponse.up_to_date:
        reponse.digest = derniere.digest
        return reponse
    snapshot = await _snapshot(fetcher, source, sha, chemin)
    trouvee = next((s for s in snapshot.skills if s.path == chemin), None)
    if trouvee is None:
        reponse.problem = f"there is no skill at `{chemin or '/'}` any more at {sha[:12]}"
    elif trouvee.problem:
        reponse.problem = trouvee.problem
    elif trouvee.name != slug:
        reponse.problem = f"the skill at `{chemin or '/'}` is now named `{trouvee.name}`"
    else:
        avant: dict[str, str] = dict(derniere.files or {})
        apres = trouvee.files
        reponse.digest = empreinte(apres)
        reponse.added = sorted(set(apres) - set(avant))
        reponse.removed = sorted(set(avant) - set(apres))
        reponse.modified = sorted(c for c in set(avant) & set(apres) if avant[c] != apres[c])
    return reponse


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
    organisation = await _organisation(session, org)
    skill = await _skill(session, organisation, slug)
    ligne = (
        await session.execute(
            select(SkillVersion).where(SkillVersion.skill_id == skill.id, SkillVersion.version == version)
        )
    ).scalar_one_or_none()
    if ligne is None:
        raise not_found("Skill version", f"{slug}@{version}")
    if ligne.review_status != "pending_review":
        raise conflict(f"`{slug}` v{version} is not waiting for a review")
    if ligne.created_by and ligne.created_by.lower() == principal.email.lower():
        raise forbidden(
            f"`{slug}` v{version} was imported by you: a second person approves a high-risk version"
        )
    ligne.review_status = "approved"
    ligne.reviewed_by = principal.email
    ligne.reviewed_at = datetime.now(UTC)
    await session.flush()
    await record(
        session,
        principal,
        "skill.approve",
        org_id=organisation.id,
        target_type="skill",
        target_id=skill.id,
        version=version,
        risk=ligne.risk,
        imported_by=ligne.created_by,
    )
    return _version_dto(ligne, files=True)
