# SPDX-License-Identifier: Apache-2.0
"""A scriptable GitHub App: installations, accounts, repositories, OAuth proofs (ADR 0073)."""

from __future__ import annotations

from typing import Any

from ..errors import UpstreamError


class FakeGitHubApp:
    """Same methods as `choregos_adapters.github.app.GitHubApp`, in memory.

    One shared instance serves a test process (`shared()`); tests script it and call `reset()`.
    """

    _shared: FakeGitHubApp | None = None

    def __init__(self) -> None:
        self.reset()

    @classmethod
    def shared(cls) -> FakeGitHubApp:
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def reset(self) -> None:
        self.slug = "choregos-bot"
        self.name = "Choregos Bot"
        #: A deployment without an App: tests that exercise the App set this to True.
        self.configured = False
        self.oauth_enabled = False
        self.unavailable = False
        self.installations: dict[int, dict[str, Any]] = {}
        self.repositories: dict[int, list[dict[str, Any]]] = {}
        self.oauth_grants: dict[str, set[int]] = {}
        self.calls: list[str] = []

    # ── scripting ──
    def add_installation(
        self,
        installation_id: int,
        login: str,
        *,
        account_type: str = "Organization",
        repository_selection: str = "selected",
        suspended: bool = False,
    ) -> None:
        self.installations[installation_id] = {
            "id": installation_id,
            "account": {"login": login, "id": installation_id * 10, "type": account_type},
            "repository_selection": repository_selection,
            "suspended_at": "2026-10-01T00:00:00Z" if suspended else None,
            "app_id": 1,
        }
        self.repositories.setdefault(installation_id, [])

    def remove_installation(self, installation_id: int) -> None:
        self.installations.pop(installation_id, None)
        self.repositories.pop(installation_id, None)

    def add_repository(
        self, installation_id: int, full_name: str, *, private: bool = True, default_branch: str = "main"
    ) -> None:
        self.repositories.setdefault(installation_id, []).append(
            {"full_name": full_name, "private": private, "default_branch": default_branch}
        )

    def grant_oauth(self, code: str, installation_ids: set[int]) -> None:
        """The person who comes back with `code` manages exactly these installations."""
        self.oauth_grants[code] = set(installation_ids)

    # ── the port ──
    def _check(self, call: str) -> None:
        self.calls.append(call)
        if self.unavailable:
            raise UpstreamError("github", "the fake GitHub is unavailable")

    async def app_info(self) -> dict[str, Any]:
        self._check("app_info")
        return {"slug": self.slug, "name": self.name}

    async def list_installations(self) -> list[dict[str, Any]]:
        self._check("list_installations")
        return [dict(i) for i in self.installations.values()]

    async def installation_repositories(self, installation_id: int) -> list[dict[str, Any]]:
        self._check("installation_repositories")
        return [dict(r) for r in self.repositories.get(installation_id, [])]

    async def user_installation_ids(self, code: str) -> set[int]:
        self._check("user_installation_ids")
        return set(self.oauth_grants.get(code, set()))
