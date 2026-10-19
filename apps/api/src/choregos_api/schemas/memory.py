# SPDX-License-Identifier: Apache-2.0
"""Named memories, global and project (S40-11, ADR 0075)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import Dto

SLUG = r"^[a-z][a-z0-9-]{1,62}$"
Reach = Literal["org", "projects"]


class MemoryStoreUser(Dto):
    """An agent whose latest version binds the memory."""

    agent: str
    version: int


class MemoryStoreDto(Dto):
    slug: str
    display_name: str
    description: str | None = None
    scope: Literal["global", "project"]
    #: The project's slug, for a project memory.
    project: str | None = None
    #: For a global memory: `org` (every project's runs) or `projects` (the listed ones).
    reach: Reach | None = None
    projects: list[str] = Field(default_factory=list)
    status: Literal["active", "archived"] = "active"
    #: `shared` and `own`: every project has them, they are never rows.
    implicit: bool = False
    created_by: str | None = None
    created_at: datetime | None = None
    used_by: list[MemoryStoreUser] = Field(default_factory=list)


class MemoryStoreCreate(Dto):
    """A global memory: curated by an organisation administrator, with a reach."""

    slug: str = Field(pattern=SLUG)
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    reach: Reach = "org"
    projects: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _reach_names_its_projects(self) -> MemoryStoreCreate:
        if self.reach == "projects" and not self.projects:
            raise ValueError("reach `projects` names at least one project")
        return self


class ProjectMemoryCreate(Dto):
    """A memory of a project: no reach to give, its runs only."""

    slug: str = Field(pattern=SLUG)
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class MemoryStorePatch(Dto):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    reach: Reach | None = None
    projects: list[str] | None = Field(default=None, max_length=200)
    status: Literal["active", "archived"] | None = None


class RunMemorySearch(Dto):
    """The sidecar's live search of the memories a run reads (S40-11)."""

    query: str = Field(min_length=1, max_length=2000)
    k: int = Field(default=5, ge=1, le=20)
    #: One memory of the run, by label (`global:security-lessons`); absent, all of them.
    memory: str | None = None


class RunMemoryHit(Dto):
    memory: str
    kind: str
    subject: str
    content: str
    score: float | None = None
