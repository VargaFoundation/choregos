# SPDX-License-Identifier: Apache-2.0
"""The `web_search` connector family (ADR 0083): a query in, links and snippets out.

Types: `brave`, `tavily`, `firecrawl` (and `fake`, in `fakes/`). `vertex-enterprise` is decided but not
shipped: it needs a gateway that passes the `enterpriseWebSearch` tool through (ADR 0060).
"""

from __future__ import annotations

from choregos_core.web_search import DataHandling

from .base import (
    MAX_RESULTS,
    SITE_PATTERN,
    SearchBackend,
    SearchError,
    SearchRateLimited,
)
from .brave import BraveSearch
from .firecrawl import FirecrawlSearch
from .tavily import TavilySearch

#: The shipped backends, by type name.
BACKENDS: dict[str, type[SearchBackend]] = {
    "brave": BraveSearch,
    "tavily": TavilySearch,
    "firecrawl": FirecrawlSearch,
}


def declared(type_name: str) -> DataHandling:
    """What a backend type declares it does with queries; an unknown type declares nothing."""
    if type_name == "fake":
        from ..fakes.web_search import FakeSearchBackend

        return FakeSearchBackend.DECLARED
    backend = BACKENDS.get(type_name)
    return backend.DECLARED if backend else DataHandling()


__all__ = [
    "BACKENDS",
    "MAX_RESULTS",
    "SITE_PATTERN",
    "BraveSearch",
    "FirecrawlSearch",
    "SearchBackend",
    "SearchError",
    "SearchRateLimited",
    "TavilySearch",
    "declared",
]
