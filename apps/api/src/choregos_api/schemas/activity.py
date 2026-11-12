# SPDX-License-Identifier: Apache-2.0
"""What is in flight in an organisation (ADR 0142 §1, ADR 0141 §2): the DTOs of `openapi.yaml`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .base import Dto

ActivityKind = Literal["run", "session", "turn"]


class SpendSoFar(Dto):
    """What an entry has spent: the gateway's count for its key while it runs, the ledger once booked."""

    tokens_in: int
    tokens_out: int
    tokens_cached: int
    cost_usd: float
    #: When the gateway was asked (at most every 30 s per run), or when the spend was booked.
    read_at: datetime
    booked: bool = False


class ActivityEntry(Dto):
    kind: ActivityKind
    id: str
    status: str
    project_slug: str | None = None
    work_item_id: str | None = None
    work_item_key: str | None = None
    workflow: str | None = None
    stage_role: str | None = None
    agent_slug: str | None = None
    assistant: str | None = None
    model: str | None = None
    initiated_by: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    last_event: str | None = None
    steps: int = 0
    #: `None` when the gateway cannot say (ADR 0141 §2): never zeros for unknown.
    spend: SpendSoFar | None = None
    can_stop: bool


class ActivityList(Dto):
    in_flight: list[ActivityEntry]
    finished: list[ActivityEntry]


__all__ = ["ActivityEntry", "ActivityKind", "ActivityList", "SpendSoFar"]
