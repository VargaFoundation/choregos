# SPDX-License-Identifier: Apache-2.0
"""`FakeObjectStore`: the object store in memory (ADR 0005), for `CHOREGOS_FAKES=1` and tests.

Scriptable: `tamper` rewrites an object behind the platform's back, which is how a test proves
that a read checks the digest; `forget` removes one; `fail_next` makes the next call refuse.
"""

from __future__ import annotations

from ..storage import ObjectNotFoundError, ObjectStoreError


class FakeObjectStore:
    def __init__(self, url: str = "memory://choregos") -> None:
        self.base = url.rstrip("/")
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.puts = 0
        self._failures: list[str] = []

    def url_of(self, key: str) -> str:
        return f"{self.base}/{key.lstrip('/')}"

    async def put(self, key: str, data: bytes, media_type: str) -> str:
        self._maybe_fail()
        self.objects[key.lstrip("/")] = (bytes(data), media_type)
        self.puts += 1
        return self.url_of(key)

    async def get(self, key: str) -> bytes:
        self._maybe_fail()
        try:
            return self.objects[key.lstrip("/")][0]
        except KeyError:
            raise ObjectNotFoundError(f"no object at {self.url_of(key)}") from None

    # ───────── staging ─────────

    def tamper(self, key: str, data: bytes) -> None:
        """Rewrites an object without the platform knowing: its recorded digest no longer matches."""
        _, media_type = self.objects[key.lstrip("/")]
        self.objects[key.lstrip("/")] = (data, media_type)

    def forget(self, key: str) -> None:
        self.objects.pop(key.lstrip("/"), None)

    def fail_next(self, message: str = "object store unavailable") -> None:
        self._failures.append(message)

    def _maybe_fail(self) -> None:
        if self._failures:
            raise ObjectStoreError(self._failures.pop(0))
