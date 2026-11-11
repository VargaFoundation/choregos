# SPDX-License-Identifier: Apache-2.0
"""Upstream updates of the knowledge an assistant offers (S63-06, ADR 0138 point 3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import Dto


class KnowledgeUpdate(Dto):
    id: str
    base: str
    path: str
    kind: Literal["add", "update", "remove"]
    diff: str
    proposed_content: str | None = None
    plugin_version: str
    status: Literal["proposed", "accepted", "merged", "refused"]
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime


class KnowledgeUpdateList(Dto):
    items: list[KnowledgeUpdate]


class UpdateDecision(Dto):
    decision: Literal["accept", "refuse", "merge"]
    #: the text to keep, for a merge
    content: str | None = Field(default=None, max_length=2_000_000)
