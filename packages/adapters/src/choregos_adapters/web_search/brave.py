# SPDX-License-Identifier: Apache-2.0
"""Brave Search API: `GET https://api.search.brave.com/res/v1/web/search`, key in `X-Subscription-Token`.

`site` is the `site:` operator in the query; `recency` is `freshness` (`pd`, `pw`, `pm`, `py`).

Data handling: **not declared**. What Brave keeps of an API query depends on the plan and its terms,
which this code has not reviewed; every field stays unknown, so a ceiling with a retention cap or
without reuse refuses it until the organisation states its contract (`data_handling`, ADR 0083 §3).
"""

from __future__ import annotations

from typing import Any, ClassVar

from .base import Recency, SearchBackend, result, with_site

URL = "https://api.search.brave.com/res/v1/web/search"
_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


class BraveSearch(SearchBackend):
    name: ClassVar[str] = "brave"

    async def _search(
        self, query: str, count: int, site: str | None, recency: Recency | None
    ) -> list[dict[str, Any] | None]:
        self._require_key()
        params: dict[str, Any] = {"q": with_site(query, site), "count": count}
        if recency:
            params["freshness"] = _FRESHNESS[recency]
        body = await self._send(
            "GET",
            URL,
            params=params,
            headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
        )
        hits = ((body or {}).get("web") or {}).get("results") or []
        return [
            result(h.get("title"), h.get("url"), h.get("description"), h.get("page_age"))
            for h in hits
            if isinstance(h, dict)
        ]
