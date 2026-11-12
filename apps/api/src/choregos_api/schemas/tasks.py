# SPDX-License-Identifier: Apache-2.0
"""Assistant tasks (ADR 0137, S63-13): the DTOs of `/me/assistants/{org}/{slug}/tasks/*` and `/me/inbox`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto

TriggerKind = Literal["on_demand", "schedule", "poll", "trigger"]


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


class WebhookSecretRequest(Dto):
    """`rotate` makes a new key (the old one stops verifying at the receiver once it is changed there)."""

    rotate: bool = False


class WebhookSecret(Dto):
    """The key an installation's task webhook is signed with, shown once to an owner (`assistant:manage`)."""

    secret: str
    generation: int
    shown_at: datetime


class TaskEventAck(Dto):
    """What the door of a task's events did with one event."""

    accepted: bool
    outcome: Literal["started", "duplicate"]
    work_item_key: str | None = None


class TaskEventKey(Dto):
    """The key and the URL of a task's event door, for whoever manages the installation: the sender signs
    the body with the key (`X-Choregos-Signature: sha256=<HMAC-SHA256 of the body>`)."""

    url: str
    secret: str
    generation: int


class TaskEventLine(Dto):
    """One line of the journal of a task's events: what was decided, never the payload."""

    task: str
    event_id: str
    outcome: Literal["started", "duplicate", "refused"]
    summary: str
    work_item_key: str | None = None
    received_at: datetime


class TaskEventList(Dto):
    items: list[TaskEventLine]
