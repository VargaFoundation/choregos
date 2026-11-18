# SPDX-License-Identifier: Apache-2.0
"""What waits for the caller (ADR 0154 §4, G8): the DTOs of `GET /me/pending`."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .base import Dto

PendingKind = Literal["human_step", "governed_action", "conversation_action", "launch"]


class PendingWhere(Dto):
    org: str | None = None
    project_slug: str | None = None
    work_item_id: str | None = None
    work_item_key: str | None = None
    conversation_id: str | None = None


class PendingDecision(Dto):
    """The route that decides it, and the console's address."""

    method: Literal["POST"] = "POST"
    path: str
    url: str


class PendingItem(Dto):
    kind: PendingKind
    id: str
    what: str
    where: PendingWhere
    since: datetime
    due_at: datetime | None = None
    decision: PendingDecision


class PendingList(Dto):
    items: list[PendingItem]
