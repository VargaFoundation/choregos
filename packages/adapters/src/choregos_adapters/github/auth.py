# SPDX-License-Identifier: Apache-2.0
"""Authentification GitHub App : JWT d'App, jetons d'installation, cache et portée minimale."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

from ..errors import ConfigurationError

GITHUB_API = "https://api.github.com"
TOKEN_SAFETY_MARGIN_S = 60


@dataclass(slots=True)
class InstallationToken:
    token: str
    expires_at: float
    repositories: tuple[str, ...] = ()

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at - TOKEN_SAFETY_MARGIN_S


@dataclass
class GitHubAppAuth:
    """Un jeton par installation et par dépôt, de durée de vie courte (§2.4).

    L'App n'a jamais de jeton « large » : `token_for()` demande exactement le dépôt
    et les permissions nécessaires, et le résultat est mis en cache jusqu'à expiration.
    """

    app_id: str
    private_key: str
    base_url: str = GITHUB_API
    #: The installation the platform claimed for the connector's organisation (ADR 0073). When set,
    #: it is used instead of discovering the installation from the repository, so a repository the
    #: organisation did not install the App on never gets a token. Injected server-side.
    pinned_installation: int | None = None
    #: The GitHub account (owner) of the pinned installation; a repository of another owner is refused.
    pinned_account: str | None = None
    _cache: dict[tuple[str, str], InstallationToken] = field(default_factory=dict)

    def app_jwt(self, ttl_s: int = 540) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + ttl_s, "iss": self.app_id}
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.app_jwt()}", "Accept": "application/vnd.github+json"}

    async def _paginate(
        self, client: httpx.AsyncClient, url: str, headers: dict[str, str], key: str | None
    ) -> list[dict[str, Any]]:
        """Every item of a paginated GitHub list (`per_page=100`, until a short page)."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            response = await client.get(url, headers=headers, params={"per_page": 100, "page": page})
            response.raise_for_status()
            body = response.json()
            items = body[key] if key else body
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1

    async def app_info(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """`GET /app`: the App's slug and name (never configured, so never stale)."""
        response = await client.get(f"{self.base_url}/app", headers=self._headers())
        response.raise_for_status()
        return dict(response.json())

    async def list_installations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """`GET /app/installations` (App JWT), every page."""
        return await self._paginate(client, f"{self.base_url}/app/installations", self._headers(), None)

    async def installation_repositories(
        self, client: httpx.AsyncClient, installation_id: int
    ) -> list[dict[str, Any]]:
        """The repositories an installation can see: an installation token without a repository
        restriction, then `GET /installation/repositories`, every page."""
        response = await client.post(
            f"{self.base_url}/app/installations/{installation_id}/access_tokens",
            headers=self._headers(),
            json={"permissions": {"metadata": "read"}},
        )
        response.raise_for_status()
        headers = {
            "Authorization": f"Bearer {response.json()['token']}",
            "Accept": "application/vnd.github+json",
        }
        return await self._paginate(
            client, f"{self.base_url}/installation/repositories", headers, "repositories"
        )

    async def installation_id_for(self, client: httpx.AsyncClient, repo: str) -> int:
        owner, name = repo.split("/", 1)
        response = await client.get(
            f"{self.base_url}/repos/{owner}/{name}/installation",
            headers={"Authorization": f"Bearer {self.app_jwt()}", "Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        return int(response.json()["id"])

    async def token_for(
        self,
        client: httpx.AsyncClient,
        repo: str,
        *,
        installation_id: int | None = None,
        permissions: dict[str, str] | None = None,
        ttl_s: int = 3600,
    ) -> InstallationToken:
        scope = ",".join(f"{k}={v}" for k, v in sorted((permissions or {}).items()))
        if installation_id is None and self.pinned_installation is not None:
            owner = repo.split("/", 1)[0]
            if self.pinned_account and owner.lower() != self.pinned_account.lower():
                raise ConfigurationError(
                    f"the GitHub installation of this connector belongs to `{self.pinned_account}`, "
                    f"not to `{owner}`: no token is issued for {repo}"
                )
            installation_id = self.pinned_installation
        cache_key = (repo, scope)
        cached = self._cache.get(cache_key)
        if cached is not None and not cached.expired:
            return cached
        if installation_id is None:
            installation_id = await self.installation_id_for(client, repo)
        name = repo.split("/", 1)[1]
        body: dict[str, Any] = {"repositories": [name]}
        if permissions:
            body["permissions"] = permissions
        response = await client.post(
            f"{self.base_url}/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self.app_jwt()}", "Accept": "application/vnd.github+json"},
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
        token = InstallationToken(
            token=payload["token"],
            expires_at=time.time() + ttl_s,
            repositories=(repo,),
        )
        self._cache[cache_key] = token
        return token


DEFAULT_PERMISSIONS = {
    "contents": "write",
    "issues": "write",
    "pull_requests": "write",
    "checks": "write",
    "metadata": "read",
}

#: What the GitHub App must be granted for the adapters to work (ADR 0067). The setup procedure of
#: the GitHub connectors renders this list and a test compares them, so a call the adapters start
#: making cannot go without the procedure learning of it. `DEFAULT_PERMISSIONS` (what a token asks
#: for) must stay inside it. `organization_projects` is the organisation's Projects v2 (GraphQL).
APP_PERMISSIONS = {
    "contents": "write",
    "issues": "write",
    "pull_requests": "write",
    "checks": "write",
    "metadata": "read",
    "organization_projects": "write",
}

#: The webhook events the platform parses (`tracker/github_events.py`).
APP_EVENTS = (
    "issues",
    "issue_comment",
    "projects_v2_item",
    "pull_request",
    "pull_request_review",
    "check_suite",
    "check_run",
)

RUNNER_PERMISSIONS = {"contents": "write", "metadata": "read"}
