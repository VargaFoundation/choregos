"""Releases, trains, findings."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto, PageMeta


class ReleaseItemDto(Dto):
    work_item_key: str
    title: str | None = None
    sha: str
    pr_url: str | None = None
    risk: str | None = None
    labels: list[str] = Field(default_factory=list)


class ReleaseDto(Dto):
    id: str
    project_slug: str
    env: str
    batch_no: int
    status: str
    items: list[ReleaseItemDto] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    approved_by: str | None = None
    verdict: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None
    promotion_url: str | None = None


class ReleasePage(Dto):
    items: list[ReleaseDto]
    meta: PageMeta


class TrainStatusDto(Dto):
    env: str
    status: str
    batch_size: int = 0
    pending_items: list[str] = Field(default_factory=list)
    next_departure: datetime | None = None
    frozen: bool = False
    freeze_reason: str | None = None
    current_release: ReleaseDto | None = None
    window_open: bool = True


class FreezeRequest(Dto):
    reason: str = Field(min_length=3)


class ApproveRequest(Dto):
    note: str | None = None


class AbortRequest(Dto):
    reason: str | None = None


class FindingDto(Dto):
    id: str
    project_slug: str
    origin_work_item_key: str | None = None
    origin_run_id: str | None = None
    title: str
    type: str
    severity: str
    evidence: str
    suggested_fix: str | None = None
    estimate: str | None = None
    status: str
    created_work_item_key: str | None = None
    duplicate_of: str | None = None
    occurrences: int = 1
    relevant: bool | None = None
    created_at: datetime


class FindingPage(Dto):
    items: list[FindingDto]
    meta: PageMeta


class FindingAction(Dto):
    action: Literal[
        "create_ticket", "mark_duplicate", "dismiss", "agent_ready", "mark_relevant", "mark_false_positive"
    ]
    duplicate_of: str | None = None
    reason: str | None = None


class FindingAck(Dto):
    accepted: bool
    finding_id: str | None = None
    remaining: int = 0
