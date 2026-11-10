# SPDX-License-Identifier: Apache-2.0
"""Assistant tasks (ADR 0137, S63-13): the DTOs of `/me/assistants/{org}/{slug}/tasks/*` and `/me/inbox`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto

TriggerKind = Literal["on_demand", "schedule", "poll"]


class TaskSubscriptionPut(Dto):
    """What a person sets: which of the task's triggers, its cron (the manifest's by default) read in
    `timezone` (an IANA name, UTC by default), and whether it is on."""

    kind: TriggerKind = "schedule"
    cron: str | None = Field(default=None, max_length=128)
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool = True


class TaskTrigger(Dto):
    kind: TriggerKind
    cron: str | None = None
    timezone: str = "UTC"


class TaskSubscription(Dto):
    task: str
    trigger: TaskTrigger
    delegation: Literal["none", "read_only"]
    deliver: list[str]
    enabled: bool
    delegation_id: str | None = None
    last_run_at: datetime | None = None
    last_refusal: str | None = None
    created_at: datetime


class TaskRun(Dto):
    """The work item a run opened, in the caller's personal space."""

    work_item_id: str
    key: str
    project: str


class InboxItem(Dto):
    """One artifact a task of the caller delivered."""

    id: str
    task: str
    installation: str
    work_item_id: str
    name: str
    version: int
    type: str
    media_type: str
    content: Any = None
    created_at: datetime


class InboxList(Dto):
    items: list[InboxItem]
