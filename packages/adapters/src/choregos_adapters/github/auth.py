"""Authentification GitHub App : JWT d'App, jetons d'installation, cache et portée minimale."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

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
    _cache: dict[tuple[str, str], InstallationToken] = field(default_factory=dict)

    def app_jwt(self, ttl_s: int = 540) -> str:
        now = int(time.time())
        payload = {"iat": now - 60, "exp": now + ttl_s, "iss": self.app_id}
        return jwt.encode(payload, self.private_key, algorithm="RS256")

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

RUNNER_PERMISSIONS = {"contents": "write", "metadata": "read"}
