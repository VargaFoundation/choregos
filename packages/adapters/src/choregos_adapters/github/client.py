"""Client GitHub : REST + GraphQL, avec backoff sur 429/secondary limits et file par installation."""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..errors import UpstreamError
from .auth import DEFAULT_PERMISSIONS, GitHubAppAuth

GITHUB_API = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"
MAX_RETRIES = 4


@dataclass
class RateLimiter:
    """Une file par installation : GitHub compte ses quotas par installation, pas par dépôt."""

    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]


class GitHubClient:
    """Enveloppe fine autour de l'API GitHub, partagée par le tracker et le SCM."""

    def __init__(
        self,
        *,
        auth: GitHubAppAuth | None = None,
        token: str | None = None,
        base_url: str = GITHUB_API,
        graphql_url: str = GRAPHQL,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.auth = auth
        self.static_token = token
        self.base_url = base_url.rstrip("/")
        self.graphql_url = graphql_url
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._limiter = RateLimiter()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _token(self, repo: str | None) -> str:
        if self.static_token:
            return self.static_token
        if self.auth is None or repo is None:
            raise UpstreamError("github", "aucune authentification configurée")
        installation = await self.auth.token_for(self._client, repo, permissions=DEFAULT_PERMISSIONS)
        return installation.token

    async def request(
        self,
        method: str,
        path: str,
        *,
        repo: str | None = None,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> Any:
        """Appel REST avec retries exponentiels sur 429, 403 secondaire et 5xx."""
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        token = await self._token(repo)
        headers = {"Authorization": f"Bearer {token}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        async with self._limiter.lock(repo or "global"):
            for attempt in range(MAX_RETRIES):
                response = await self._client.request(method, url, headers=headers, json=json, params=params)
                if response.status_code == 429 or (
                    response.status_code == 403 and "secondary rate limit" in response.text.lower()
                ):
                    await asyncio.sleep(self._retry_after(response, attempt))
                    continue
                if response.status_code >= 500:
                    await asyncio.sleep(self._retry_after(response, attempt))
                    continue
                if response.status_code >= 400:
                    raise UpstreamError(
                        "github",
                        f"{method} {path} → {response.status_code} : {response.text[:300]}",
                        retry_after=self._reset_in(response),
                        status_code=response.status_code,
                    )
                if response.status_code == 204 or not response.content:
                    return None
                return response.json()
        raise UpstreamError("github", f"{method} {path} : quota épuisé après {MAX_RETRIES} tentatives")

    @staticmethod
    def _retry_after(response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("retry-after")
        if header and header.isdigit():
            return float(header)
        return float(min(60.0, (2**attempt) + random.random()))  # noqa: S311 - jitter, pas de crypto

    @staticmethod
    def _reset_in(response: httpx.Response) -> int | None:
        reset = response.headers.get("x-ratelimit-reset")
        if reset and reset.isdigit():
            return max(0, int(reset) - int(time.time()))
        return None

    async def graphql(
        self, query: str, variables: dict[str, Any], *, repo: str | None = None
    ) -> dict[str, Any]:
        token = await self._token(repo)
        response = await self._client.post(
            self.graphql_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"query": query, "variables": variables},
        )
        if response.status_code >= 400:
            raise UpstreamError("github", f"graphql → {response.status_code} : {response.text[:300]}")
        payload = response.json()
        if payload.get("errors"):
            raise UpstreamError("github", f"graphql : {payload['errors']}")
        data: dict[str, Any] = payload.get("data", {})
        return data

    async def paginate(
        self, path: str, *, repo: str | None = None, params: dict[str, Any] | None = None
    ) -> list[Any]:
        out: list[Any] = []
        page = 1
        while True:
            batch = await self.request(
                "GET", path, repo=repo, params={**(params or {}), "per_page": 100, "page": page}
            )
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return out
