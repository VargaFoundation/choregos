# SPDX-License-Identifier: Apache-2.0
"""What is in flight in an organisation (ADR 0142 §1, ADR 0141 §2): the DTOs of `openapi.yaml`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

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


class ActivityFailure(Dto):
    """Why a run ended `failed` (ADR 0187 §3): its stage result's reason and summary."""

    #: An open string: `loop`, `budget`, `max_turns`, `timeout`, a gateway refusal, ...
    reason: str
    #: At most 300 characters, credentials redacted.
    summary: str = Field(max_length=300)


class ActivityEntry(Dto):
    kind: ActivityKind
    id: str
    status: str
    project_slug: str | None = None
    work_item_id: str | None = None
    work_item_key: str | None = None
    #: The work item's title (ADR 0154 §4, G6): a surface shows more than a key.
    work_item_title: str | None = None
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
    #: Only a run whose status is `failed`; `None` for any other entry.
    failure: ActivityFailure | None = None


class ActivityList(Dto):
    in_flight: list[ActivityEntry]
    finished: list[ActivityEntry]


__all__ = ["ActivityEntry", "ActivityFailure", "ActivityKind", "ActivityList", "SpendSoFar"]
