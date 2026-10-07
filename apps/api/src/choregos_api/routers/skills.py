# SPDX-License-Identifier: Apache-2.0
"""La bibliothèque de skills de l'organisation (ADR 0033).

Une skill est un dossier qu'un agent porte ; ses versions ne se modifient jamais. Ce qui entre — par
JSON ou par une archive zip — est vérifié par `services.skills` : un en-tête `name` qui est celui de
la skill, une `description`, aucune permission déclarée, aucune sortie du dossier.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import Agent, AgentVersion, Organization, Skill, SkillVersion
from ..deps import Db, Me
from ..errors import conflict, forbidden, not_found, unprocessable
from ..rbac import Permission
from ..schemas import SkillDto, SkillFiles, SkillVersionDto
from ..services.skills import SkillRefusee, empreinte, lire_le_zip, valider

router = APIRouter(tags=["agents"])

Slug = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
#: Une archive plus grosse que ça n'a pas besoin d'être ouverte pour être refusée.
ARCHIVE_MAX = 2 * 1024 * 1024


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
        versions=[
            SkillVersionDto(
                version=v.version, digest=v.digest, created_by=v.created_by, created_at=v.created_at
            )
            for v in versions
        ]
        if avec_versions
        else None,
    )


async def _publier(
    session: AsyncSession,
    principal: Me,
    organisation: Organization,
    fichiers: dict[str, str],
    slug: str | None,
) -> tuple[Skill, SkillVersion]:
    """Crée la skill (version 1) ou publie sa version suivante — jamais de réécriture."""
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
        fichiers = lire_le_zip(contenu)
    except SkillRefusee as refus:
        raise unprocessable(str(refus)) from refus
    skill, _ = await _publier(session, principal, organisation, fichiers, None)
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
    return SkillVersionDto(
        version=version.version,
        digest=version.digest,
        created_by=version.created_by,
        created_at=version.created_at,
    )


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
    return SkillVersionDto(
        version=ligne.version,
        digest=ligne.digest,
        created_by=ligne.created_by,
        created_at=ligne.created_at,
        files=ligne.files,
    )
