# SPDX-License-Identifier: Apache-2.0
"""Tavily: `POST https://api.tavily.com/search`, key as a bearer token.

`site` is `include_domains`; `recency` is `time_range`. The page content Tavily can return
(`include_raw_content`) is never asked for: search returns links and snippets, reading goes through
`web_fetch` (ADR 0083 §1).

Data handling: **not declared** (not reviewed against Tavily's terms): every field stays unknown until
the organisation states its contract.
"""

from __future__ import annotations

from typing import Any, ClassVar

from .base import Recency, SearchBackend, result

URL = "https://api.tavily.com/search"


class TavilySearch(SearchBackend):
    name: ClassVar[str] = "tavily"

    async def _search(
        self, query: str, count: int, site: str | None, recency: Recency | None
    ) -> list[dict[str, Any] | None]:
        self._require_key()
        payload: dict[str, Any] = {
            "query": query,
            "max_results": count,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
        }
        if site:
            payload["include_domains"] = [site]
        if recency:
            payload["time_range"] = recency
        body = await self._send(
            "POST", URL, json=payload, headers={"Authorization": f"Bearer {self._api_key}"}
        )
        hits = (body or {}).get("results") or []
        return [
            result(h.get("title"), h.get("url"), h.get("content"), h.get("published_date"))
            for h in hits
            if isinstance(h, dict)
        ]
