# SPDX-License-Identifier: Apache-2.0
"""A minimal REST client shared by the Jira and GitLab trackers.

Neither has the subtleties of GitHub (App, installations, secondary limits): a static token, a
`base_url` and a backoff on 429 and 5xx are enough. Everything goes through an injectable
`httpx.AsyncClient`, so that the tests speak the protocol without a network.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from ..errors import UpstreamError

MAX_RETRIES = 4


class RestClient:
    """A thin wrapper: `request`, `paginate`, bounded backoff, explicit errors."""

    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
        service: str = "rest",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service = service
        self._headers = {"Accept": "application/json", **(headers or {})}
        self._auth = auth
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {**self._headers, **(kwargs.pop("headers", None) or {})}
        as_text = bool(kwargs.pop("as_text", False))  # a plain-text body (a job trace), not JSON
        last: httpx.Response | None = None
        for attempt in range(MAX_RETRIES):
            response = await self._client.request(method, url, headers=headers, auth=self._auth, **kwargs)
            if response.status_code < 400:
                if response.status_code == 204 or not response.content:
                    return {}
                return response.text if as_text else response.json()
            last = response
            if response.status_code not in {429, 500, 502, 503, 504}:
                break
            await asyncio.sleep(_backoff(response, attempt))
        detail = (last.text if last is not None else "")[:500]
        status_code = last.status_code if last is not None else 0
        raise UpstreamError(
            self.service,
            f"{status_code} sur {method} {path} : {detail}",
            status_code=status_code,
        )

    async def paginate(
        self, path: str, *, params: dict[str, Any] | None = None, limit: int = 500
    ) -> list[Any]:
        """Page-by-page pagination: GitLab returns a list, Jira an object holding an array."""
        out: list[Any] = []
        page = 1
        while len(out) < limit:
            payload = await self.request(
                "GET", path, params={**(params or {}), "page": page, "per_page": 100}
            )
            batch = payload if isinstance(payload, list) else []
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return out[:limit]


def _backoff(response: httpx.Response, attempt: int) -> float:
    """Honours `Retry-After` when it is there; otherwise exponential with a little jitter."""
    retry_after = response.headers.get("Retry-After")
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), 30.0)
    return float(min(2**attempt + random.random(), 30.0))  # noqa: S311 - wait, not crypto
