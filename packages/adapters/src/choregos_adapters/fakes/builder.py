# SPDX-License-Identifier: Apache-2.0
"""`FakeImageBuilder`: builds in memory (ADR 0005, ADR 0080 §9), for `CHOREGOS_FAKES=1` and tests.

Like the real builders, a key already built is found, not built again: `builds` lists the keys that
really built, `requests` every call. `fail(key, reason)` makes that key's next build fail as a
`pip install` would; `hold()` keeps the next builds waiting until `release()`, so a test can look at
a version while its build runs.
"""

from __future__ import annotations

import asyncio
import hashlib

from ..oci.builder import BuildError, BuildRequest, BuildResult


class FakeImageBuilder:
    name = "fake"

    def __init__(self) -> None:
        self.images: dict[str, str] = {}
        self.builds: list[str] = []
        self.requests: list[BuildRequest] = []
        self.failures: dict[str, str] = {}
        self._gate: asyncio.Event | None = None

    async def build(self, request: BuildRequest) -> BuildResult:
        self.requests.append(request)
        if request.key in self.images:
            return BuildResult(self.images[request.key], self.name, built=False)
        if self._gate is not None:
            await self._gate.wait()
        if request.key in self.failures:
            raise BuildError(self.failures.pop(request.key))
        digest = "sha256:" + hashlib.sha256(f"{request.dockerfile}\n{request.key}".encode()).hexdigest()
        image = f"{request.repository}@{digest}"
        self.images[request.key] = image
        self.builds.append(request.key)
        return BuildResult(image, self.name, built=True, log="built in memory")

    # ───────── staging ─────────

    def fail(self, key: str, reason: str = "pip install: no matching distribution") -> None:
        self.failures[key] = reason

    def hold(self) -> None:
        self._gate = asyncio.Event()

    def release(self) -> None:
        if self._gate is not None:
            self._gate.set()
            self._gate = None
