# SPDX-License-Identifier: Apache-2.0
"""The GitHub App as a platform actor: who installed it, what each installation sees (ADR 0073).

`GitHubAppAuth` mints tokens; this port adds what the API needs to *discover* installations and to
check that the person who claims one really manages it (GitHub user authorization). `FakeGitHubApp`
(`fakes/github_app.py`) has the same methods.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Protocol, TypeVar

import httpx

from ..errors import ConfigurationError, UpstreamError
from .auth import GITHUB_API, GitHubAppAuth

GITHUB_WEB = "https://github.com"
_T = TypeVar("_T")


class GitHubAppPort(Protocol):
    """What `choregos_api.services.github_app` needs from GitHub."""

    @property
    def oauth_enabled(self) -> bool: ...

    async def app_info(self) -> dict[str, Any]: ...

    async def list_installations(self) -> list[dict[str, Any]]: ...

    async def installation_repositories(self, installation_id: int) -> list[dict[str, Any]]: ...

    async def user_installation_ids(self, code: str) -> set[int]: ...


class GitHubApp:
    """The real port: App JWT calls, and the OAuth exchange of the App's client."""

    def __init__(
        self,
        *,
        app_id: str,
        private_key: str,
        client_id: str = "",
        client_secret: str = "",
        base_url: str = GITHUB_API,
        web_url: str = GITHUB_WEB,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not app_id or not private_key:
            raise ConfigurationError(
                "the GitHub App is not configured (CHOREGOS_GITHUB_APP_ID / _PRIVATE_KEY)"
            )
        self._auth = GitHubAppAuth(app_id=app_id, private_key=private_key, base_url=base_url)
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._web_url = web_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=20)

    @property
    def oauth_enabled(self) -> bool:
        return bool(self._client_id and self._client_secret)

    async def app_info(self) -> dict[str, Any]:
        return await self._call(self._auth.app_info(self._client))

    async def list_installations(self) -> list[dict[str, Any]]:
        return await self._call(self._auth.list_installations(self._client))

    async def installation_repositories(self, installation_id: int) -> list[dict[str, Any]]:
        return await self._call(self._auth.installation_repositories(self._client, installation_id))

    async def user_installation_ids(self, code: str) -> set[int]:
        """Exchange the OAuth `code` for the person's token, then list the installations that person
        can access (`GET /user/installations`). The proof that they manage what they claim."""
        if not self.oauth_enabled:
            raise ConfigurationError(
                "the GitHub App has no OAuth client: CHOREGOS_GITHUB_APP_CLIENT_SECRET is not set"
            )
        try:
            exchange = await self._client.post(
                f"{self._web_url}/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={"client_id": self._client_id, "client_secret": self._client_secret, "code": code},
            )
            exchange.raise_for_status()
            token = exchange.json().get("access_token")
            if not token:
                raise UpstreamError("github", "the authorization code was refused")
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
            ids: set[int] = set()
            page = 1
            while True:
                response = await self._client.get(
                    f"{self._base_url}/user/installations",
                    headers=headers,
                    params={"per_page": 100, "page": page},
                )
                response.raise_for_status()
                items = response.json().get("installations", [])
                ids.update(int(i["id"]) for i in items)
                if len(items) < 100:
                    return ids
                page += 1
        except httpx.HTTPError as exc:
            raise UpstreamError("github", f"user authorization failed: {exc}") from exc

    @staticmethod
    async def _call(call: Awaitable[_T]) -> _T:
        try:
            return await call
        except httpx.HTTPStatusError as exc:
            raise UpstreamError(
                "github", f"{exc.request.method} {exc.request.url.path} -> {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("github", str(exc)) from exc
