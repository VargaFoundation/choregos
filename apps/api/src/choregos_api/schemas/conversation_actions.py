# SPDX-License-Identifier: Apache-2.0
"""Conversation actions (ADR 0139 point 5): the DTOs of `/me/conversations/{id}/actions`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    created_at: datetime


class ConversationActionList(Dto):
    items: list[ConversationAction]
