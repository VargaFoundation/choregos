# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases (ADR 0051): a curated corpus of the organisation, and its documents."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .base import Dto

SLUG = r"^[a-z][a-z0-9-]{1,62}$"
Scope = Literal["org", "projects", "groups"]
ServingMode = Literal["files", "index"]
DocumentStatus = Literal["pending", "converting", "ready", "failed"]


def _scope_names_its_members(scope: str | None, projects: list[str] | None, groups: list[str] | None) -> None:
    if scope == "projects" and not projects:
        raise ValueError("scope `projects` names at least one project")
    if scope == "groups" and not groups:
        raise ValueError("scope `groups` names at least one group")


class KnowledgeBaseCreate(Dto):
    slug: str = Field(pattern=SLUG)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    scope: Scope = "org"
    projects: list[str] = Field(default_factory=list, max_length=200)
    groups: list[str] = Field(default_factory=list, max_length=200)
    serving_mode: ServingMode = "files"
    #: people read it only through a grant; a run is served it only for a person who reads it (ADR 0097)
    restricted: bool = False

    @model_validator(mode="after")
    def _scope(self) -> KnowledgeBaseCreate:
        _scope_names_its_members(self.scope, self.projects, self.groups)
        return self


class ProjectKnowledgeBaseCreate(Dto):
    """A base created from a project: scoped to it and owned by it, so there is no scope to give."""

    slug: str = Field(pattern=SLUG)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    serving_mode: ServingMode = "files"


class KnowledgeBasePatch(Dto):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    scope: Scope | None = None
    projects: list[str] | None = Field(default=None, max_length=200)
    groups: list[str] | None = Field(default=None, max_length=200)
    serving_mode: ServingMode | None = None
    status: Literal["active", "archived"] | None = None
    #: `agent:manage` only, like the rest of the reach (S42-11)
    restricted: bool | None = None


GrantSubject = Literal["user", "group", "project"]
GrantRole = Literal["viewer", "editor", "owner"]


class KnowledgeGrantDto(Dto):
    """A role on one base given to a person, a group or a project (S42-11, ADR 0081)."""

    subject_type: GrantSubject
    #: a member id or an e-mail (`user`), an IdP group name (`group`), a project slug (`project`)
    subject: str = Field(min_length=1, max_length=320)
    display: str | None = None
    role: GrantRole
    #: read-only: an e-mail with no member yet, kept until that person signs in
    pending: bool = False
    created_by: str | None = None
    created_at: datetime | None = None


class KnowledgeGrants(Dto):
    grants: list[KnowledgeGrantDto] = Field(default_factory=list, max_length=200)


class KnowledgeGrantsPut(Dto):
    grants: list[KnowledgeGrantDto] = Field(default_factory=list, max_length=200)


class KnowledgePermissions(Dto):
    """What the caller may do with a base, decided by the server (ADR 0081)."""

    edit: bool
    distill: bool
    share: bool
    archive: bool
    widen: bool


class KnowledgeDocumentDto(Dto):
    id: str
    filename: str
    title: str
    mime: str
    size_bytes: int
    digest: str
    source: str
    status: DocumentStatus
    error: str | None = None
    #: what a ready conversion left out (S42-09e): a spreadsheet cut at its row cap, sheet by sheet
    warnings: list[str] = Field(default_factory=list)
    created_by: str | None = None
    created_at: datetime | None = None
    converted_at: datetime | None = None
    #: Returned by the reading of ONE document; a listing does not carry it.
    markdown: str | None = None
    #: synced from a source (S33-04): which one, and where to open the original
    source_id: str | None = None
    source_url: str | None = None


class KnowledgeBaseDto(Dto):
    slug: str
    name: str
    description: str | None = None
    scope: Scope
    projects: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    serving_mode: ServingMode
    status: str
    document_count: int = 0
    #: documents by status: `{"ready": 3, "failed": 1}`
    documents_by_status: dict[str, int] = Field(default_factory=dict)
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    #: Returned by the reading of ONE knowledge base; a listing does not carry them.
    documents: list[KnowledgeDocumentDto] | None = None
    #: `index` mode: chunks, documents indexed, and how the search ranks here
    index: dict[str, Any] | None = None
    eval_set: list[dict[str, Any]] = Field(default_factory=list)
    #: the last run of the eval set: `recall` (at `k`), per question, `mode`, `ran_at`
    eval_result: dict[str, Any] | None = None
    #: where its documents are synced from (S33-04); a reading of ONE base carries them
    sources: list[KnowledgeSourceDto] | None = None
    #: the last distillation into a principles skill (S33-05)
    distillation: dict[str, Any] | None = None
    #: the project this base was created from (S39-14): its owners curate it and widen its reach
    owner_project: str | None = None
    #: people read it only through a grant; a run is served it only for a person who reads it (ADR 0097)
    restricted: bool = False
    #: what the caller may do, and the server's sentence when it may not edit (S42-11)
    can: KnowledgePermissions | None = None
    cannot_reason: str | None = None
    #: the caller's role through grants or ownership; None for a plain reader through the reach
    my_role: GrantRole | None = None
    #: a reading of ONE base carries them, to a caller who may share it
    grants: list[KnowledgeGrantDto] | None = None


class KnowledgeEvalCase(Dto):
    question: str = Field(min_length=1, max_length=2000)
    #: the documents expected among the first results, by file name or title
    expected: list[str] = Field(min_length=1, max_length=20)


class KnowledgeEvalSet(Dto):
    cases: list[KnowledgeEvalCase] = Field(default_factory=list, max_length=200)


class KnowledgeSearchRequest(Dto):
    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)
    #: one base of the run (internal search); absent, all of them
    knowledge_base: str | None = None


class KnowledgeCitation(Dto):
    knowledge_base: str
    document_id: str
    document: str
    filename: str
    heading_path: list[str] = Field(default_factory=list)
    excerpt: str
    score: float


class KnowledgeSearchResult(Dto):
    #: hybrid (full-text + pgvector) | full-text (no pgvector, or no embedder) | in-process (SQLite)
    mode: str
    hits: list[KnowledgeCitation] = Field(default_factory=list)


SourceKind = Literal["sharepoint", "confluence", "web", "git"]
#: interval: every `interval_minutes`; cron: `refresh_cron` in `refresh_timezone`; manual: on demand only
RefreshMode = Literal["interval", "cron", "manual"]


class KnowledgeSourceCreate(Dto):
    kind: SourceKind
    name: str = Field(min_length=1, max_length=200)
    #: `sharepoint`, `confluence`: the organisation connector (capability `documents`) holding the
    #: credentials;
    #: `git`, `web`: optional, for a private repository or a site behind a login (`documents/web`)
    connector: str | None = None
    #: `sharepoint`: `site_id` or `drive_id`, optional `folder`; `confluence`: `space_key`, optional
    #: `page_id` (that page and its descendants), `include_root`, `include_attachments`, `labels`;
    #: `web`: `url`, `max_depth`, `max_pages`, `exclude`, `include_documents`, `headers` (plain ones, never a
    #: credential); `git`: `url`, `ref`, `paths`
    config: dict[str, Any] = Field(default_factory=dict)
    interval_minutes: int = Field(default=60, ge=5, le=7 * 24 * 60)
    refresh_mode: RefreshMode = "interval"
    #: five fields (minute hour day month weekday), at least 5 minutes between two fires
    refresh_cron: str | None = Field(default=None, max_length=100)
    #: an IANA name, for example `Europe/Paris`
    refresh_timezone: str = Field(default="UTC", max_length=64)
    #: Open the source's hosts in the organisation's egress allowlist in the same call (S39-22):
    #: without it a missing host is a 409 `egress_required`. Needs `tools:grant`.
    allow_egress: bool = False


class KnowledgeSourcePatch(Dto):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=7 * 24 * 60)
    refresh_mode: RefreshMode | None = None
    refresh_cron: str | None = Field(default=None, max_length=100)
    refresh_timezone: str | None = Field(default=None, max_length=64)
    allow_egress: bool = False


class KnowledgeSourceDto(Dto):
    id: str
    kind: SourceKind
    name: str
    connector: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    interval_minutes: int
    refresh_mode: RefreshMode = "interval"
    refresh_cron: str | None = None
    refresh_timezone: str = "UTC"
    #: an estimate: the worker may be down at that moment; absent in manual mode
    next_sync_at: datetime | None = None
    sync_requested_at: datetime | None = None
    status: str
    last_synced_at: datetime | None = None
    last_error: str | None = None
    last_summary: dict[str, Any] | None = None
    document_count: int = 0


class KnowledgeDistillRequest(Dto):
    """Optional body of `POST …/distill` (S42-10): documents left out and a spending cap."""

    exclude: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=20)
    max_usd: float | None = Field(default=None, ge=0)


class DistillBiggest(Dto):
    filename: str
    characters: int


class KnowledgeDistillEstimate(Dto):
    """What distilling a base would read and cost, before any model call."""

    characters: int
    tokens_estimate: int
    documents: int
    parts: int
    calls: int
    model: str
    input_cost_usd: float | None = None
    output_cost_usd: float | None = None
    priced_by: Literal["gateway", "override", "none"]
    budget_usd: float | None = None
    within_budget: bool
    refused_reason: str | None = None
    biggest: list[DistillBiggest]
