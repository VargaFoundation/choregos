# SPDX-License-Identifier: Apache-2.0
"""The shape every document source has (S33-04)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SourceItem:
    """One document at the source: a stable `ref`, a file `name` (its extension says its format), a
    `version` that changes when its content does, and where a human can open it."""

    ref: str
    name: str
    version: str
    url: str | None = None
    size: int | None = None


@dataclass
class Changes:
    #: what was added or may have changed since the cursor
    items: list[SourceItem] = field(default_factory=list)
    #: refs deleted at the source since the cursor
    deleted: list[str] = field(default_factory=list)
    #: the cursor to keep once these changes are applied (a Graph deltaLink, a commit)
    cursor: dict[str, Any] | None = None
    #: `items` is everything the source holds: whatever it does not list was deleted
    full: bool = False
    #: refs the source holds but could not be read this time (a 500): never taken for deletions
    unreadable: list[str] = field(default_factory=list)


class DocumentSource(Protocol):
    async def changes(self, cursor: dict[str, Any] | None) -> Changes: ...

    async def fetch(self, item: SourceItem) -> bytes: ...


class SourceRefusedError(ValueError):
    """A source configuration the platform refuses (a private address, a non-https URL…)."""
