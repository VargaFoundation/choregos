# SPDX-License-Identifier: Apache-2.0
"""The DTOs of an assistant's data protection (S63-16): the reads journal, the erasure and the export."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .assistant_memory import MemorySubject
from .base import Dto, PageMeta


class AssistantReadLine(Dto):
    """One tool call of an assistant's conversation: who it was for, what it asked, about whom. Never the
    content read."""

    id: str
    user_id: str
    conversation_id: str | None = None
    work_item_key: str | None = None
    need: str
    operation: str
    subject_type: str | None = None
    subject_key: str | None = None
    delegation_id: str | None = None
    at: datetime


class AssistantReadPage(Dto):
    items: list[AssistantReadLine]
    meta: PageMeta


class SubjectErasureRequest(Dto):
    """What an auditor asks about one subject: `export` it, or `erase` it. The conversations where the subject
    was resolved are redacted by default (their messages replaced, their metadata kept) or `delete`d."""

    subject: MemorySubject
    mode: Literal["export", "erase"]
    conversations: Literal["redact", "delete"] = "redact"


class SubjectMemoryFact(Dto):
    id: str
    content: str
    status: str
    created_at: datetime | None = None


class SubjectMemory(Dto):
    """One person's memory of the subject in one space of the installation."""

    user_id: str
    space: str
    facts: list[SubjectMemoryFact] = Field(default_factory=list)
    profiles: list[dict[str, Any]] = Field(default_factory=list)


class SubjectConversation(Dto):
    """A conversation where the subject was resolved: metadata only."""

    id: str
    user_id: str
    title: str | None = None
    created_at: datetime | None = None
    last_activity_at: datetime | None = None


class SubjectErasure(Dto):
    """The answer: the export lists what was found; the erasure says what it removed."""

    mode: Literal["export", "erase"]
    subject: MemorySubject
    memories: list[SubjectMemory] = Field(default_factory=list)
    conversations: list[SubjectConversation] = Field(default_factory=list)
    memories_erased: int = 0
    conversations_redacted: int = 0
    conversations_deleted: int = 0


class UserErasure(Dto):
    """What `DELETE /me/assistants/{org}/{slug}` removed of the caller's data in the installation."""

    conversations: int
    memory_stores: int
    memory_facts: int
    memory_profiles: int
    #: What the other holders of a person's data removed (connected accounts, delegations: lot 2), by name.
    others: dict[str, int] = Field(default_factory=dict)
