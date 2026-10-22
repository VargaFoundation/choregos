# SPDX-License-Identifier: Apache-2.0
"""The fake of the `web_search` family (ADR 0005): deterministic results, scriptable.

Without a script, a query gets three results under `https://docs.example.org/` whose paths come from
the query's SHA-256, so the same query always gives the same links. Scripting: `results[query]`
replaces the results of a query, `errors[query]` makes it raise, `rate_limited` makes every call a
429; `calls` records every call (query and filters).
Used under `CHOREGOS_FAKES=1`, by the tests, the e2e and the demo.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, ClassVar

from choregos_core.web_search import DataHandling

from ..web_search.base import Recency, SearchError, SearchRateLimited, result

HOST = "docs.example.org"


@dataclass
class FakeSearchBackend:
    name: ClassVar[str] = "fake"
    #: The fake keeps nothing and runs in the process.
    DECLARED: ClassVar[DataHandling] = DataHandling(
        query_retention_days=0, query_reuse=False, region="local", terms_ref="choregos:fake"
    )

    results: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)
    rate_limited: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        site: str | None = None,
        recency: Recency | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append({"query": query, "max_results": max_results, "site": site, "recency": recency})
        if self.rate_limited:
            raise SearchRateLimited("fake: rate limited (429)", 7)
        if query in self.errors:
            raise self.errors[query]
        if not query.strip():
            raise SearchError("query refused: an empty query")
        if query in self.results:
            found = list(self.results[query])
        else:
            digest = hashlib.sha256(query.encode()).hexdigest()
            host = site or HOST
            found = []
            for i in range(3):
                link = f"https://{host}/{digest[i * 8 : i * 8 + 8]}"
                made = result(f"{query} — result {i + 1}", link, f"A page about {query}.")
                if made is not None:
                    found.append(made)
        return found[: max(1, int(max_results))]

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "backend": self.name}

    def reset(self) -> None:
        self.results.clear()
        self.errors.clear()
        self.rate_limited = False
        self.calls.clear()
