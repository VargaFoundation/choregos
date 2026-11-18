# SPDX-License-Identifier: Apache-2.0
"""A document source in memory (ADR 0005): SharePoint, OneDrive, a web site or a git repository
under `CHOREGOS_FAKES`, scripted by tests and demos — `put` a file, `delete` it, and the next
`changes` says so, delta-style (since the cursor) like Graph."""

from __future__ import annotations

from typing import Any

from ..documents.base import Changes, SourceItem


class FakeDocumentSource:
    def __init__(self) -> None:
        self.files: dict[str, tuple[str, bytes, int]] = {}
        self.tombstones: dict[str, int] = {}
        self.clock = 0
        self.fetched: list[str] = []

    def put(self, ref: str, name: str, content: bytes) -> None:
        self.clock += 1
        self.files[ref] = (name, content, self.clock)
        self.tombstones.pop(ref, None)

    def delete(self, ref: str) -> None:
        self.clock += 1
        self.files.pop(ref, None)
        self.tombstones[ref] = self.clock

    async def changes(self, cursor: dict[str, Any] | None) -> Changes:
        since = int((cursor or {}).get("clock", 0))
        return Changes(
            items=[
                SourceItem(
                    ref=ref, name=name, version=str(at), url=f"https://fake.source/{ref}", size=len(data)
                )
                for ref, (name, data, at) in sorted(self.files.items())
                if at > since
            ],
            deleted=sorted(ref for ref, at in self.tombstones.items() if at > since),
            cursor={"clock": self.clock},
        )

    async def fetch(self, item: SourceItem) -> bytes:
        self.fetched.append(item.ref)
        return self.files[item.ref][1]


#: One fake per knowledge source, shared in the process like the platform's other fakes.
FAKE_SOURCES: dict[str, FakeDocumentSource] = {}


def fake_source(key: str) -> FakeDocumentSource:
    return FAKE_SOURCES.setdefault(key, FakeDocumentSource())


class FakeDocumentsConnector:
    """The `documents` connector under `CHOREGOS_FAKES`: each source is its own scripted fake."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def source(self, config: dict[str, Any], *, key: str) -> FakeDocumentSource:
        return fake_source(key)
