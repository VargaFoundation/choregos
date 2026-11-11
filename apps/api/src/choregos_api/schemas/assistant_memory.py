# SPDX-License-Identifier: Apache-2.0
"""A person's memory page of an assistant (ADR 0136 point 6): the DTOs of
`/me/assistants/{org}/{slug}/memory`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto


class MemorySubject(Dto):
    type: str
    key: str


class MemoryFactView(Dto):
    id: str
    content: str
    status: str
    pinned: bool = False
    reason: str | None = None
    created_at: datetime | None = None


class MemoryProfileVersion(Dto):
    version: int
    status: str
    document: dict[str, Any]
    updated_by: str | None = None
    created_at: datetime | None = None


class MemorySubjectSpace(Dto):
    """What is remembered in one store: of the person (no subject) or of one subject."""

    subject: MemorySubject | None = None
    facts: list[MemoryFactView] = Field(default_factory=list)
    profiles: list[MemoryProfileVersion] = Field(default_factory=list)


class MemorySpace(Dto):
    space: str
    scope: Literal["installation", "user", "user_subject"]
    kind: Literal["facts", "profile"]
    writes: str
    subjects: list[MemorySubjectSpace] = Field(default_factory=list)


class MemoryAction(Dto):
    """What the person does to a proposal or a fact: confirm or reject a proposed fact or profile version,
    pin or unpin a fact, correct a fact's content or write a profile document, or `add` what the agent may
    not write itself (`user_only`): a fact's `content` or a profile `document`, active at once."""

    action: Literal["confirm", "reject", "pin", "unpin", "correct", "add"]
    fact_id: str | None = None
    version: int | None = Field(default=None, ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    document: dict[str, Any] | None = None


class MemoryExport(Dto):
    """Everything an assistant remembers of the person (their `user` and `user_subject` spaces): facts
    awaiting confirmation or active, and every version of every profile."""

    installation: str
    organisation: str
    exported_at: datetime
    spaces: list[MemorySpace] = Field(default_factory=list)
