# SPDX-License-Identifier: Apache-2.0
"""The catalogue of default skills, and installing one (ADR 0065, ADR 0050).

An entry is an offer. Installing publishes an ordinary skill version through the same door as an
import, so the supply-chain gate runs again: the risk the catalogue carries is information, never a
pass. A bundled entry is read from the image and checked against its provenance; a git entry is
fetched at the commit the catalogue pins.
"""

from __future__ import annotations

from typing import Annotated, Literal

from choregos_core import skill_catalogue as catalogue
from fastapi import APIRouter, Path, Query, status
from sqlalchemy import select

from ..audit import record
from ..config import get_settings
from ..db.models import Skill, SkillVersion
from ..deps import Config, Db, Me
from ..errors import conflict, forbidden, not_found, unprocessable, upstream
from ..rbac import Permission
from ..schemas import CatalogueMetaDto, SkillCatalogueDto, SkillCatalogueEntryDto, SkillDto
from ..schemas.catalogues import SkillCatalogueInstall
from ..services.catalogues import egress_of
from ..services.egress import egress_scope, ensure_or_409, skill_import_targets
from ..services.skill_sources import GitSource, SourceRefused, allowed, get_fetcher, parse_repo_url
from ..services.skills import empreinte, publish_version
from .skills import _dto, _lire, _organisation, _skill_sources, _snapshot

router = APIRouter(tags=["agents"])

EntryId = Annotated[str, Path(pattern=r"^[a-z][a-z0-9-]{1,62}$")]
#: Where a git entry's tarball comes from: the API pod must be able to reach it.
GIT_HOSTS = ["codeload.github.com"]
State = Literal["available", "installed", "update", "pending_review", "blocked"]


def _entry_source(entry: catalogue.SkillEntry) -> GitSource:
    try:
        return parse_repo_url(entry.repo)
    except SourceRefused as refusal:  # a catalogue file the generator could not have written
        raise unprocessable(f"`{entry.id}`: {refusal}") from refusal


def _permission_problem(
    principal: Me, org: str, source: GitSource, sources: list[str], exists: bool
) -> str | None:
    """Why this person cannot install the entry, or `None`. ADR 0050 §4, unchanged: an administrator
    installs anything; a project owner a new skill from an allowlisted repository."""
    if principal.can(Permission.AGENT_MANAGE, org):
        return None
    if not principal.can(Permission.SKILL_IMPORT, org):
        return "installing a skill needs the project_owner or org_admin role"
    if not allowed(source, sources):
        return f"{source.key} is not in the organisation's skill sources: an administrator installs it"
    if exists:
        return "a project owner installs new skills only: a new version needs an administrator"
    return None


async def _latest(session: Db, org_id: str, ids: list[str]) -> dict[str, SkillVersion]:
    """The latest version of each of the organisation's skills with one of these names."""
    rows = (
        await session.execute(
            select(SkillVersion, Skill.slug)
            .join(Skill, Skill.id == SkillVersion.skill_id)
            .where(Skill.org_id == org_id, Skill.slug.in_(ids))
            .order_by(Skill.slug, SkillVersion.version.desc())
        )
    ).all()
    latest: dict[str, SkillVersion] = {}
    for version, slug in rows:
        latest.setdefault(slug, version)
    return latest


def _from_this_entry(version: SkillVersion, entry_id: str) -> bool:
    return str((version.source or {}).get("catalogue") or "").startswith(f"{entry_id}@")


def _state(
    entry: catalogue.SkillEntry, latest: SkillVersion | None, permission: str | None
) -> tuple[State, str | None]:
    """What the entry means in this organisation: computed, never stored."""
    if latest is not None and not _from_this_entry(latest, entry.id):
        return "blocked", (
            f"the organisation already has a skill named `{entry.id}` that does not come from this entry: "
            "it is left untouched"
        )
    if latest is not None and latest.review_status == "pending_review":
        return "pending_review", None
    if latest is not None and latest.digest == entry.digest:
        return "installed", None
    if permission:
        return "blocked", permission
    return ("update" if latest is not None else "available"), None


@router.get(
    "/orgs/{org}/skill-catalogue", response_model=SkillCatalogueDto, operation_id="listSkillCatalogue"
)
async def list_skill_catalogue(
    org: str,
    session: Db,
    principal: Me,
    q: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=40)] = None,
    delivery: Annotated[Literal["bundled", "git"] | None, Query()] = None,
) -> SkillCatalogueDto:
    """The shipped catalogue and what each entry means in this organisation. Its members can read it."""
    _lire(principal, org)
    organisation = await _organisation(session, org)
    loaded = catalogue.load()
    sources, _ = await _skill_sources(session, organisation)
    latest = await _latest(session, organisation.id, [e.id for e in loaded.entries])
    declared = get_settings().api_egress_hosts
    needle = (q or "").lower().strip()
    entries: list[SkillCatalogueEntryDto] = []
    for e in loaded.entries:
        if needle and needle not in " ".join([e.id, e.title, e.publisher, e.description]).lower():
            continue
        if (category and category not in e.categories) or (delivery and e.delivery != delivery):
            continue
        version = latest.get(e.id)
        permission = _permission_problem(principal, org, _entry_source(e), sources, version is not None)
        state, blocked = _state(e, version, permission)
        entries.append(
            SkillCatalogueEntryDto.model_validate(
                e.model_dump(mode="json")
                | {
                    "state": state,
                    "installed_version": version.version if version is not None else None,
                    "blocked_reason": blocked,
                    "egress": egress_of(GIT_HOSTS, declared) if e.delivery == "git" else "unknown",
                }
            )
        )
    return SkillCatalogueDto(meta=CatalogueMetaDto.model_validate(loaded.meta.model_dump()), entries=entries)


@router.post(
    "/orgs/{org}/skill-catalogue/{entry_id}/install",
    response_model=SkillDto,
    status_code=status.HTTP_201_CREATED,
    operation_id="installSkillCatalogueEntry",
)
async def install_skill_catalogue_entry(
    org: str,
    entry_id: EntryId,
    session: Db,
    principal: Me,
    settings: Config,
    body: SkillCatalogueInstall | None = None,
) -> SkillDto:
    """Publishes the entry as a skill (v1) or, when the catalogue pins a newer one, its next version."""
    entry = catalogue.entry(entry_id)
    if entry is None:
        raise not_found("Skill catalogue entry", entry_id)
    organisation = await _organisation(session, org)
    source = _entry_source(entry)
    sources, _ = await _skill_sources(session, organisation)
    existing = (await _latest(session, organisation.id, [entry.id])).get(entry.id)
    if problem := _permission_problem(principal, org, source, sources, existing is not None):
        raise forbidden(problem)
    if existing is not None:
        if not _from_this_entry(existing, entry.id):
            raise conflict(
                f"the organisation already has a skill named `{entry.id}` that does not come from this "
                "catalogue entry: it is left untouched"
            )
        if existing.digest == entry.digest:
            raise conflict(f"`{entry.id}` is already installed at this version (v{existing.version})")

    if entry.delivery != "bundled":
        # Fetched from git: the repository's hosts must be open (S39-22), as for an import.
        await ensure_or_409(
            session,
            principal,
            organisation,
            skill_import_targets(source.url),
            allow_egress=body.allow_egress if body else False,
            origin=f"skill-catalogue:{entry.id}",
            reason=f"installing the skill {entry.id} from {source.key}",
            settings=settings,
        )
    async with egress_scope(session, organisation, "api", f"skill-catalogue:{entry.id}", settings):
        files, executables, repo_licences = await _files_of(entry, source, settings.fakes)
    if empreinte(files) != entry.digest:
        raise unprocessable(
            f"`{entry.id}` is not what the catalogue pinned (digest mismatch): the catalogue is regenerated "
            "from the pinned commit, so this is a defect, not something to override"
        )
    version_label = catalogue.load().meta.catalogue_version
    provenance: dict[str, object] = {
        "repo": entry.repo,
        "sha": entry.sha,
        "path": entry.path,
        "licence": entry.licence,
        "catalogue": f"{entry.id}@{version_label}",
        "delivery": entry.delivery,
    }
    skill, version = await publish_version(
        session,
        principal,
        organisation,
        files,
        entry.id,
        provenance,
        executables=executables,
        repo_licences=repo_licences,
        review_as_platform=entry.delivery == "bundled",
    )
    await record(
        session,
        principal,
        "skill.catalogue_install",
        org_id=organisation.id,
        target_type="skill",
        target_id=skill.id,
        entry=entry.id,
        catalogue_version=version_label,
        version=version.version,
        delivery=entry.delivery,
        risk=version.risk,
        review_status=version.review_status,
    )
    return await _dto(session, organisation, skill, avec_versions=True)


async def _files_of(
    entry: catalogue.SkillEntry, source: GitSource, fakes: bool
) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """The files to publish, the executables the gate reads, and the licence texts it reads."""
    if entry.delivery == "bundled":
        try:
            bundle = catalogue.read_bundled(entry.id)
        except catalogue.BundleTamperedError as tampered:
            raise upstream(
                "image", f"a bundled skill does not match its provenance: {tampered}"
            ) from tampered
        return bundle.files, [], bundle.repo_licences
    snapshot = await _snapshot(get_fetcher(fakes), source, entry.sha, entry.path)
    found = next((s for s in snapshot.skills if s.path == entry.path), None)
    if found is None:
        raise unprocessable(f"there is no skill at `{entry.path or '/'}` in {entry.repo} at {entry.sha[:12]}")
    if found.problem:
        raise unprocessable(f"`{entry.id}` ({entry.path}): {found.problem}")
    if found.name != entry.id:
        raise unprocessable(f"the skill at `{entry.path}` is named `{found.name}`, not `{entry.id}`")
    return found.files, found.executables, snapshot.licences
