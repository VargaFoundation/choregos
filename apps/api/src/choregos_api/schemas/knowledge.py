# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases (ADR 0051): a curated corpus of the organisation, and its documents."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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

    @model_validator(mode="after")
    def _scope(self) -> KnowledgeBaseCreate:
        _scope_names_its_members(self.scope, self.projects, self.groups)
        return self


class KnowledgeBasePatch(Dto):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    scope: Scope | None = None
    projects: list[str] | None = Field(default=None, max_length=200)
    groups: list[str] | None = Field(default=None, max_length=200)
    serving_mode: ServingMode | None = None
    status: Literal["active", "archived"] | None = None


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


SourceKind = Literal["sharepoint", "web", "git"]


class KnowledgeSourceCreate(Dto):
    kind: SourceKind
    name: str = Field(min_length=1, max_length=200)
    #: `sharepoint`: the organisation connector (capability `documents`) holding the credentials
    connector: str | None = None
    #: `sharepoint`: `site_id` or `drive_id`, optional `folder`; `web`: `url`, `max_depth`, `max_pages`;
    #: `git`: `url`, `ref`, `paths`
    config: dict[str, Any] = Field(default_factory=dict)
    interval_minutes: int = Field(default=60, ge=5, le=7 * 24 * 60)


class KnowledgeSourcePatch(Dto):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    interval_minutes: int | None = Field(default=None, ge=5, le=7 * 24 * 60)


class KnowledgeSourceDto(Dto):
    id: str
    kind: SourceKind
    name: str
    connector: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    interval_minutes: int
    status: str
    last_synced_at: datetime | None = None
    last_error: str | None = None
    last_summary: dict[str, Any] | None = None
    document_count: int = 0
