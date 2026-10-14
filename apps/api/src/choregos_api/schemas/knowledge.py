# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases (ADR 0051): a curated corpus of the organisation, and its documents."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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
