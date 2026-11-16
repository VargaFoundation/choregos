# SPDX-License-Identifier: Apache-2.0
"""Conversation actions (ADR 0139 point 5): the DTOs of `/me/conversations/{id}/actions`."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import StringConstraints

from .base import Dto

ActionStatus = Literal["proposed", "confirmed", "executed", "refused", "expired", "governed"]


class ConversationAction(Dto):
    id: str
    conversation_id: str
    need: str
    operation: str
    arguments: dict[str, Any]
    summary: str
    status: ActionStatus
    #: `write` (a write need's operation) or `launch` (the start of a project's workflow, ADR 0158).
    kind: Literal["write", "launch"] = "write"
    #: For a launch: the project and the workflow the proposal names.
    target: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    #: What the source's preview of the write returned at the proposal, when it serves one.
    diff: dict[str, Any] | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    executed_at: datetime | None = None
    #: Why the person refused it (ADR 0154 §7); null when it was not refused, or refused without a reason.
    refusal_reason: str | None = None
    created_at: datetime


class ConversationActionRefusal(Dto):
    """Why a proposed write or launch is refused: never blank (surrounding spaces do not count)."""

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ConversationActionList(Dto):
    items: list[ConversationAction]
