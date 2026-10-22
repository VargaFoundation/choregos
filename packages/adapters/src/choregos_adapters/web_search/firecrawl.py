# SPDX-License-Identifier: Apache-2.0
"""Firecrawl's search: `POST {base_url}/v1/search`, on Firecrawl's cloud or a self-hosted instance.

`site` is the `site:` operator; `recency` is `tbs` (`qdr:d`, `qdr:w`, `qdr:m`, `qdr:y`).
**No `scrapeOptions` is ever sent**: with them Firecrawl fetches each result from its own
infrastructure, outside the run's allowlist and robots.txt as Choregos reads them (ADR 0083,
"Alternatives rejected"). Reading a result goes through `web_fetch`.

Data handling: **not declared**. A self-hosted instance still sends the query to the engines it
searches with; neither that nor the cloud's terms are reviewed here, so every field stays unknown
until the organisation states it.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .base import Recency, SearchBackend, result, with_site

BASE_URL = "https://api.firecrawl.dev"
_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


class FirecrawlSearch(SearchBackend):
    name: ClassVar[str] = "firecrawl"

    def __init__(self, *, base_url: str = BASE_URL, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._base_url = (base_url or BASE_URL).rstrip("/")

    async def _search(
        self, query: str, count: int, site: str | None, recency: Recency | None
    ) -> list[dict[str, Any] | None]:
        if self._base_url == BASE_URL:
            self._require_key()  # a self-hosted instance may run without one
        payload: dict[str, Any] = {"query": with_site(query, site), "limit": count}
        if recency:
            payload["tbs"] = _TBS[recency]
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        body = await self._send("POST", f"{self._base_url}/v1/search", json=payload, headers=headers)
        hits = (body or {}).get("data") or []
        return [
            result(h.get("title"), h.get("url"), h.get("description")) for h in hits if isinstance(h, dict)
        ]
