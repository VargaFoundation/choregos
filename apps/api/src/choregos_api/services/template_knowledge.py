# SPDX-License-Identifier: Apache-2.0
"""The knowledge bases a template ships (S51-06, ADR 0117): `defaults.knowledge`.

An agent names the bases it reads by slug (`spec.knowledge`), and the run refuses a base that does not
exist or is not served to its project. A template (and so a plugin, ADR 0093) that ships such agents
now ships the bases too, in the shape of the CLI's `kind: KnowledgeBase` manifest, less what the project
decides (scope, uploads, eval set).

When a project is born of the template, each base is:

- **created** when the organisation has no base of that slug: owned by the project and served to it
  (`scope: projects`), its sources attached and their sync loops started once the project is committed —
  the loop's first pass syncs (ADR 0051);
- **completed** when the project already owns it (the template applied again): the sources it lacks, by
  name, are added; none is duplicated (ADR 0008);
- **reused untouched** when it is already served to the project: it is the organisation's;
- **served** to the project when another project of the same template created it and none of its sources
  reads a project's repository: the template's own material, the same for every project;
- otherwise **left alone**, the project born without it and the audit log saying why (a base another
  project curates, or one that reads another project's repository, is never widened by a template).

A git source names its repository (`config.url`) or the template input that holds it
(`config.url_from_input`); a `github-repo` input left empty is the project's repository. The project's own
GitHub repository is read through the deployment's GitHub App (`github_app: true`), with a token minted at
each sync from the installation the organisation claimed (ADR 0073), `contents: read` only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..db.models import KnowledgeBase, KnowledgeSource, Organization, Project
from ..errors import unprocessable
from ..knowledge.access import served_to_project
from ..logging import get_logger
from ..rbac import SYSTEM

logger = get_logger("choregos.template_knowledge")

SLUG = r"^[a-z][a-z0-9-]{1,62}$"
#: `owner/name`, as a `github-repo` input holds it.
_OWNER_NAME = re.compile(r"^[\w.-]+/[\w.-]+$")
_GITHUB_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?$", re.IGNORECASE)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitSourceConfig(_Strict):
    url: str | None = None
    #: the template input that holds the repository (`owner/name` or an https URL)
    url_from_input: str | None = None
    ref: str = "main"
    paths: list[str] | None = None

    @model_validator(mode="after")
    def _one_repository(self) -> GitSourceConfig:
        if (self.url is None) == (self.url_from_input is None):
            raise ValueError("a git source names `url` or `url_from_input`, exactly one")
        if self.url is not None and not self.url.startswith("https://"):
            raise ValueError(f"a git source is reached over https: `{self.url}`")
        if self.ref.startswith("-"):
            raise ValueError(f"not a git ref: `{self.ref}`")
        return self


class TemplateKnowledgeSource(_Strict):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["git"]
    #: an organisation `documents/git` connector, for a private repository that is not the project's
    connector: str | None = None
    config: GitSourceConfig
    interval_minutes: int = Field(default=60, ge=5, le=7 * 24 * 60)
    refresh_mode: Literal["interval", "cron", "manual"] = "interval"
    refresh_cron: str | None = Field(default=None, max_length=100)
    refresh_timezone: str = Field(default="UTC", max_length=64)

    @model_validator(mode="after")
    def _refresh(self) -> TemplateKnowledgeSource:
        from ..knowledge.refresh import check_cron

        if self.refresh_mode == "cron":
            if not self.refresh_cron:
                raise ValueError(f"source `{self.name}`: a `cron` refresh needs a `refresh_cron`")
            check_cron(self.refresh_cron, self.refresh_timezone)
            self.refresh_cron = " ".join(self.refresh_cron.split())
        else:
            self.refresh_cron = None
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(self.refresh_timezone)
            except Exception as unknown:
                raise ValueError(f"unknown time zone `{self.refresh_timezone}`") from unknown
        return self


class TemplateKnowledgeBase(_Strict):
    slug: str = Field(pattern=SLUG)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    serving_mode: Literal["files", "index"] = "files"
    sources: list[TemplateKnowledgeSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_sources(self) -> TemplateKnowledgeBase:
        names = [s.name for s in self.sources]
        if len(set(names)) != len(names):
            raise ValueError(f"knowledge base `{self.slug}`: two sources share a name")
        return self

    @property
    def reads_a_project(self) -> bool:
        """Whether a source depends on the project (its repository comes from an input)."""
        return any(s.config.url_from_input is not None for s in self.sources)


def parse_template_knowledge(entries: Any, inputs: Any, template: str) -> list[TemplateKnowledgeBase]:
    """`defaults.knowledge` of a manifest, checked (422 with the reason): a template on disk is not
    validated against its schema when a project is born, so the shapes are checked here."""
    if not entries:
        return []
    if not isinstance(entries, list):
        raise unprocessable(f"template `{template}`: `defaults.knowledge` is a list")
    declared = {str(i.get("name")) for i in inputs or [] if isinstance(i, dict)}
    bases: list[TemplateKnowledgeBase] = []
    for index, entry in enumerate(entries):
        try:
            base = TemplateKnowledgeBase.model_validate(entry)
        except (ValidationError, ValueError) as invalid:
            raise unprocessable(
                f"template `{template}`: `defaults.knowledge[{index}]` is invalid: {invalid}"
            ) from invalid
        for source in base.sources:
            name = source.config.url_from_input
            if name is not None and name not in declared:
                raise unprocessable(
                    f"template `{template}`: source `{source.name}` of `{base.slug}` reads the input "
                    f"`{name}`, which the template does not declare"
                )
        bases.append(base)
    slugs = [b.slug for b in bases]
    if len(set(slugs)) != len(slugs):
        raise unprocessable(f"template `{template}` ships a knowledge base twice")
    return bases


def repository_url(value: str) -> str | None:
    """An https URL as is; `owner/name` as its GitHub URL; anything else is no repository."""
    value = value.strip()
    if value.startswith("https://"):
        return value
    if _OWNER_NAME.fullmatch(value):
        return f"https://github.com/{value.removesuffix('.git')}"
    return None


def github_repository(url: str) -> str | None:
    """`owner/name` of a GitHub https URL, `None` for any other."""
    found = _GITHUB_URL.fullmatch(url.strip())
    return f"{found.group(1)}/{found.group(2)}" if found else None


def same_repository(a: str, b: str) -> bool:
    """Two URLs of one repository, whatever the case, a `.git` suffix or a trailing slash."""

    def key(url: str) -> str:
        return url.strip().lower().rstrip("/").removesuffix(".git")

    return bool(a and b) and key(a) == key(b)


def _template_of(author: str | None) -> str | None:
    """The template name in `template:<name>[@<version>]`."""
    if not author or not author.startswith("template:"):
        return None
    return author.removeprefix("template:").partition("@")[0]


@dataclass(frozen=True)
class _Birth:
    """The project a template's bases are installed for, and what resolves their sources."""

    project: Project
    org: str
    author: str
    #: the inputs the template declares, by name
    declared: dict[str, dict[str, Any]]
    #: the inputs the project was given
    given: dict[str, Any]
    #: the project's repository (`config.repo.url`), empty without one
    repo: str


def _source_url(source: TemplateKnowledgeSource, birth: _Birth) -> tuple[str | None, str | None]:
    """The repository a source reads, or why it has none."""
    if source.config.url is not None:
        return source.config.url, None
    name = str(source.config.url_from_input)
    declaration = birth.declared.get(name, {})
    value = birth.given.get(name) or declaration.get("default")
    if not value and declaration.get("type") == "github-repo":
        value = birth.repo
    if not value:
        return None, f"the input `{name}` has no value and the project names no repository"
    url = repository_url(str(value))
    if url is None:
        return None, f"the input `{name}` is neither `owner/name` nor an https URL: `{value}`"
    return url, None


def _source_config(source: TemplateKnowledgeSource, url: str, repo: str) -> dict[str, Any]:
    """What `POST …/sources` would store for a git source, plus `github_app` for the project's own
    GitHub repository read without a connector."""
    config: dict[str, Any] = {"url": url, "ref": source.config.ref, "paths": source.config.paths or ["*.md"]}
    if source.connector is None and same_repository(url, repo) and github_repository(url) is not None:
        config["github_app"] = True
    return config


async def install_template_knowledge(
    session: AsyncSession,
    project: Project,
    bases: list[TemplateKnowledgeBase],
    inputs: list[dict[str, Any]],
    author: str,
) -> dict[str, list[str]]:
    """The bases of `bases` for `project` (see the module); returns what was done, by slug."""
    done: dict[str, list[str]] = {"created": [], "completed": [], "reused": [], "served": [], "skipped": []}
    if not bases:
        return done
    birth = _Birth(
        project=project,
        org=(
            await session.execute(select(Organization.slug).where(Organization.id == project.org_id))
        ).scalar_one(),
        author=author,
        declared={str(i.get("name")): i for i in inputs if isinstance(i, dict)},
        given=dict((project.provision_state or {}).get("inputs") or {}),
        repo=str(((project.config or {}).get("repo") or {}).get("url") or ""),
    )
    for entry in bases:
        base = (
            await session.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.org_id == project.org_id, KnowledgeBase.slug == entry.slug
                )
            )
        ).scalar_one_or_none()
        if base is None:
            base = KnowledgeBase(
                org_id=project.org_id,
                slug=entry.slug,
                name=entry.name,
                description=entry.description,
                scope="projects",
                projects=[project.slug],
                groups=[],
                owner_project=project.slug,
                serving_mode=entry.serving_mode,
                restricted=False,
                status="active",
                created_by=author,
            )
            session.add(base)
            await session.flush()
            await record(session, SYSTEM, "knowledge.create", org_id=project.org_id,
                         target_type="knowledge_base", target_id=base.id, scope=base.scope,
                         owner_project=project.slug, via=author)  # fmt: skip
            done["created"].append(entry.slug)
        elif base.owner_project == project.slug:
            done["completed"].append(entry.slug)
        elif served_to_project(base, project.slug):
            done["reused"].append(entry.slug)
            continue
        else:
            reason = _why_not_served(base, entry, author)
            if reason is not None:
                await _skip(session, project, entry.slug, reason, author)
                done["skipped"].append(entry.slug)
                continue
            base.projects = sorted({*(base.projects or []), project.slug})
            await record(session, SYSTEM, "knowledge.update", org_id=project.org_id,
                         target_type="knowledge_base", target_id=base.id, projects=base.projects,
                         via=author)  # fmt: skip
            done["served"].append(entry.slug)
            continue
        await _add_missing_sources(session, birth, base, entry)
    await session.flush()
    return done


def _why_not_served(base: KnowledgeBase, entry: TemplateKnowledgeBase, author: str) -> str | None:
    """Why a base of this slug, not served to the project, is not served to it now; `None` to serve it."""
    if base.status != "active":
        return f"the knowledge base `{base.slug}` exists and is archived"
    if _template_of(base.created_by) != _template_of(author):
        return (
            f"the knowledge base `{base.slug}` exists and is not served to this project: "
            "an administrator or its owners serve it"
        )
    if base.restricted or base.scope != "projects":
        return (
            f"the knowledge base `{base.slug}` was changed since the template created it: it is left as it is"
        )
    if entry.reads_a_project:
        return (
            f"the knowledge base `{base.slug}` reads the repository of project `{base.owner_project}`: "
            "serving it here would show that project's documents to this one"
        )
    return None


async def _skip(session: AsyncSession, project: Project, slug: str, reason: str, author: str) -> None:
    logger.warning("template.knowledge.skip", project=project.id, knowledge=slug, reason=reason)
    await record(session, SYSTEM, "template.knowledge.skip", org_id=project.org_id, target_type="project",
                 target_id=project.id, knowledge=slug, reason=reason, via=author)  # fmt: skip


async def _add_missing_sources(
    session: AsyncSession, birth: _Birth, base: KnowledgeBase, entry: TemplateKnowledgeBase
) -> None:
    """The sources of `entry` the base lacks, by name; each one's sync loop starts after the commit."""
    from ..db.session import after_commit

    present = set(
        (
            await session.execute(
                select(KnowledgeSource.name).where(KnowledgeSource.knowledge_base_id == base.id)
            )
        ).scalars()
    )
    for spec in entry.sources:
        if spec.name in present:
            continue
        url, reason = _source_url(spec, birth)
        if url is None:
            await _skip(session, birth.project, entry.slug, f"source `{spec.name}`: {reason}", birth.author)
            continue
        source = KnowledgeSource(
            org_id=base.org_id,
            knowledge_base_id=base.id,
            kind=spec.kind,
            name=spec.name,
            connector=spec.connector,
            config=_source_config(spec, url, birth.repo),
            interval_minutes=spec.interval_minutes,
            refresh_mode=spec.refresh_mode,
            refresh_cron=spec.refresh_cron,
            refresh_timezone=spec.refresh_timezone,
            next_sync_at=None,  # due: the loop's first pass syncs
            status="idle",
            created_by=birth.author,
        )
        session.add(source)
        await session.flush()
        await record(session, SYSTEM, "knowledge.source.create", org_id=base.org_id,
                     target_type="knowledge_source", target_id=source.id, kind=source.kind,
                     via=birth.author)  # fmt: skip
        after_commit(session, _sync_loop_starter(birth.org, source.id, source.interval_minutes))


def _sync_loop_starter(org: str, source_id: str, minutes: int) -> Any:
    """Starts the source's loop, as `POST …/sources` does; the worker restarts a lost start."""

    async def start() -> None:
        from ..temporal import get_temporal, knowledge_sync_id

        await get_temporal().start_knowledge_sync(
            knowledge_sync_id(source_id),
            {"org": org, "source_id": source_id, "interval_seconds": minutes * 60},
        )

    return start


__all__ = [
    "TemplateKnowledgeBase",
    "TemplateKnowledgeSource",
    "github_repository",
    "install_template_knowledge",
    "parse_template_knowledge",
    "repository_url",
    "same_repository",
]
