# SPDX-License-Identifier: Apache-2.0
"""Forks, watchers, stars and the inbox items of an agent (ADR 0152)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import Dto

WatchLevel = Literal["all", "releases"]
AgentNotificationKind = Literal[
    "version_published",
    "production_moved",
    "candidate_started",
    "candidate_promoted",
    "candidate_withdrawn",
    "rolled_back",
    "forked",
    "lifecycle_changed",
]


class AgentSocial(Dto):
    """The counters of an agent and what the caller does with it. `forks` counts the forks the caller
    may see, so two people can read two numbers."""

    watchers: int = 0
    stars: int = 0
    forks: int = 0
    #: The caller's watch level, `None` when they do not watch.
    watching: WatchLevel | None = None
    starred_by_me: bool = False


class AgentWatchPut(Dto):
    level: WatchLevel = "all"


class AgentForkDto(Dto):
    """A fork the caller may see, with how far it has moved from what it copied."""

    slug: str
    display_name: str
    scope: Literal["platform", "org", "project", "personal"]
    #: The slug of its project, for a `project` fork.
    project: str | None = None
    owner: str | None = None
    created_at: datetime | None = None
    #: The version of the source this fork copied.
    forked_from_version: int | None = None
    latest_version: int
    #: Its own versions go past the first, which is the copy: it has been edited since.
    diverged: bool = False
    lifecycle: Literal["draft", "active", "deprecated", "retired"] = "active"


class AgentForkList(Dto):
    items: list[AgentForkDto]


class AgentNotificationDto(Dto):
    id: str
    kind: AgentNotificationKind
    agent: str
    #: The fork a `forked` item names.
    fork: str | None = None
    actor: str | None = None
    version: int | None = None
    previous_version: int | None = None
    #: What the move was (`promote`, `rollback`, `publish`) or the lifecycle reached.
    detail: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class AgentNotificationList(Dto):
    items: list[AgentNotificationDto]
    unread: int = Field(default=0, description="Unread items the caller may still read")


class AgentNotificationsRead(Dto):
    updated: int
