# SPDX-License-Identifier: Apache-2.0
"""Firecrawl's search: `POST {base_url}/v1/search`, on a **self-hosted** Firecrawl instance only.

Decision of 2026-10-23 (ADR 0086, S40-01k): "Firecrawl must be local, not SaaS". `base_url` is
required, has no default, and a host under `firecrawl.dev` (Firecrawl's cloud) is refused, here and
when the connector is saved. A connector stored before this, which relied on the cloud default, has no
`base_url` and fails closed with an explicit error: nothing is sent.

`site` is the `site:` operator; `recency` is `tbs` (`qdr:d`, `qdr:w`, `qdr:m`, `qdr:y`).
**No `scrapeOptions` is ever sent**: with them Firecrawl fetches each result from its own
infrastructure, outside the run's allowlist and robots.txt as Choregos reads them (ADR 0083,
"Alternatives rejected"). Reading a result goes through `web_fetch`.

Data handling: **not declared**. "Local" does not mean the query never leaves: a self-hosted
Firecrawl still sends the query to the engines it searches with (SearXNG, or whatever its upstream
is), and neither those engines' terms nor the instance's own logging are reviewed here. Every field
stays unknown until the organisation states it.
"""

from __future__ import annotations

from typing import Any, ClassVar
from urllib.parse import urlsplit

from .base import Recency, SearchBackend, SearchError, result, with_site

#: Firecrawl's cloud: refused (decision of 2026-10-23). The domain itself and every subdomain.
CLOUD_DOMAIN = "firecrawl.dev"
_TBS = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}


def checked_base_url(raw: object) -> str:
    """The self-hosted instance's address, without a trailing slash, or a `SearchError`.

    Refused: no address (a connector stored when the cloud was the default), anything that is not an
    `http(s)` URL with a host, and Firecrawl's cloud (`firecrawl.dev` and its subdomains)."""
    text = str(raw or "").strip()
    if not text:
        raise SearchError(
            "firecrawl: base_url is required. Choregos talks to a self-hosted Firecrawl only "
            "(decision of 2026-10-23, ADR 0086): set the address of your instance"
        )
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError as bad:
        raise SearchError("firecrawl: base_url is not a valid URL") from bad
    if parts.scheme not in {"http", "https"} or not host:
        raise SearchError("firecrawl: base_url must be an http(s) URL with a host")
    if host == CLOUD_DOMAIN or host.endswith(f".{CLOUD_DOMAIN}"):
        raise SearchError(
            f"firecrawl: `{host}` is Firecrawl's cloud, which Choregos refuses: Firecrawl must be "
            "self-hosted, not SaaS (decision of 2026-10-23, ADR 0086). Point base_url at your own instance"
        )
    return text.rstrip("/")


class FirecrawlSearch(SearchBackend):
    name: ClassVar[str] = "firecrawl"
    #: Self-hosted by the organisation: its address is theirs (often in the cluster), not a public endpoint.
    PROXIED: ClassVar[bool] = False

    def __init__(self, *, base_url: str, **kwargs: Any) -> None:
        checked = checked_base_url(base_url)  # before the parent opens a client
        super().__init__(**kwargs)
        self._base_url = checked

    async def _search(
        self, query: str, count: int, site: str | None, recency: Recency | None
    ) -> list[dict[str, Any] | None]:
        payload: dict[str, Any] = {"query": with_site(query, site), "limit": count}
        if recency:
            payload["tbs"] = _TBS[recency]
        # A self-hosted instance may run without a key.
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        body = await self._send("POST", f"{self._base_url}/v1/search", json=payload, headers=headers)
        hits = (body or {}).get("data") or []
        return [
            result(h.get("title"), h.get("url"), h.get("description")) for h in hits if isinstance(h, dict)
        ]
