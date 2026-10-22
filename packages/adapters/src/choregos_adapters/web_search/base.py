# SPDX-License-Identifier: Apache-2.0
"""The `SearchBackend` port (ADR 0083): a query in, links and snippets out, never a page.

A backend is called by the API only (`services/web_search.py`), never by a runner: its key stays on the
platform's side. Each backend declares what it does with the queries (`DECLARED`); the organisation's
ceiling decides whether that is acceptable, before any query leaves.

Secrets: the key is a constructor argument, sent in a header. It never appears in a result, in an
error message (`scrub`) or in `repr()`.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, Literal

import httpx
from choregos_core.web_search import DataHandling

from ..errors import AdapterError

USER_AGENT = "choregos-web-search/1.0 (+https://github.com/varga-foundation/choregos)"
#: The most results one call returns, whatever is asked.
MAX_RESULTS = 10
SNIPPET_CHARS = 500
Recency = Literal["day", "week", "month", "year"]
#: A host name only: `site` narrows a search, it cannot smuggle operators into the query.
SITE_PATTERN = r"^(?=.{1,253}$)([A-Za-z0-9-]{1,63}\.)+[A-Za-z]{2,63}$"
_SITE = re.compile(SITE_PATTERN)


class SearchError(AdapterError):
    """The backend could not answer."""


class SearchRateLimited(SearchError):  # noqa: N818 - a named refusal, rendered as a 429
    def __init__(self, message: str, retry_after_s: int = 60) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


def result(title: Any, url: Any, snippet: Any, published_at: Any = None) -> dict[str, Any] | None:
    """One normalised result, or None when the backend gave no usable `http(s)` link."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return None
    found: dict[str, Any] = {
        "title": str(title or "")[:300],
        "url": url,
        "snippet": " ".join(str(snippet or "").split())[:SNIPPET_CHARS],
    }
    if published_at:
        found["published_at"] = str(published_at)
    return found


class SearchBackend:
    """What every search backend offers: `search()`, and the declaration of what it keeps."""

    #: The backend's type name, as the connector and the ledger (`search:<name>`) say it.
    name: ClassVar[str] = "search"
    #: What the backend does with a query, reviewed against its published terms. Unknown is `None`.
    DECLARED: ClassVar[DataHandling] = DataHandling()

    def __init__(
        self,
        *,
        api_key: str = "",
        timeout_s: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=timeout_s, transport=transport, headers={"User-Agent": USER_AGENT}
        )

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        site: str | None = None,
        recency: Recency | None = None,
    ) -> list[dict[str, Any]]:
        """At most `max_results` results for `query`, each `{title, url, snippet, published_at?}`."""
        if not isinstance(query, str) or not query.strip():
            raise SearchError("query refused: an empty query")
        if site is not None and not _SITE.fullmatch(site):
            raise SearchError("site refused: a host name is expected, e.g. docs.python.org")
        count = max(1, min(int(max_results), MAX_RESULTS))
        found = await self._search(query.strip(), count, site, recency)
        return [r for r in found if r is not None][:count]

    async def _search(
        self, query: str, count: int, site: str | None, recency: Recency | None
    ) -> list[dict[str, Any] | None]:
        raise NotImplementedError

    async def test(self) -> dict[str, Any]:
        return {"ok": True, "backend": self.name}

    # ── helpers ─────────────────────────────────────────────────────────────────────────────
    def scrub(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    def _require_key(self) -> None:
        if not self._api_key:
            raise SearchError(f"{self.name}: no api_key configured")

    async def _send(self, method: str, url: str, **kwargs: Any) -> Any:
        """One request, its JSON answer. 429 is `SearchRateLimited`, any other failure `SearchError`;
        no retry: a search is cheap to ask again and a retry storm burns a quota."""
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as down:
            # The class only: an httpx message can carry the URL.
            raise SearchError(f"{self.name}: network error ({type(down).__name__})") from down
        if response.status_code == 429:
            wait = response.headers.get("retry-after", "")
            raise SearchRateLimited(f"{self.name}: rate limited (429)", int(wait) if wait.isdigit() else 60)
        if response.status_code in {401, 403}:
            raise SearchError(f"{self.name}: the key was refused (HTTP {response.status_code})")
        if response.status_code >= 400:
            raise SearchError(f"{self.name}: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as bad:
            raise SearchError(f"{self.name}: the answer is not JSON") from bad


def with_site(query: str, site: str | None) -> str:
    return f"{query} site:{site}" if site else query
