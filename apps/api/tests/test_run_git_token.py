"""A run's git token: limited to its repository, one hour, `contents: write` (S22-07).

`docs/security.md` promised it ("issued per run, repository scope, 1 h, in memory") and the code
did not do it: the runner cloned without credentials, and the dev tenant's private sandbox
answered "Repository not found" on every run, on 08/10.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import AsyncClient

from .kits.run_git_token import run_token_for, run_with_repository


@pytest.fixture
def github_app(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A GitHub App set up, and its token minting intercepted: we keep what it was asked for."""
    from choregos_adapters.github import GitHubAppAuth, InstallationToken

    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "5242171")
    monkeypatch.setenv(
        "CHOREGOS_GITHUB_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"
    )
    requests: list[dict[str, Any]] = []

    async def mint(self: Any, client: Any, repo: str, **options: Any) -> InstallationToken:
        requests.append({"repo": repo, **options})
        return InstallationToken(token="ghs_of_the_run", expires_at=time.time() + 3600, repositories=(repo,))

    monkeypatch.setattr(GitHubAppAuth, "token_for", mint)
    return requests


async def test_a_run_receives_a_token_limited_to_its_repository(
    client: AsyncClient, project: dict[str, Any], github_app: list[dict[str, Any]]
) -> None:
    run_id = await run_with_repository(project, "https://github.com/DiametralGroup/choregos-sandbox-dev.git")
    response = await client.post(f"/api/v1/internal/runs/{run_id}/git-token", headers=run_token_for(run_id))
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["token"], body["repository"]) == ("ghs_of_the_run", "DiametralGroup/choregos-sandbox-dev")
    assert body["expires_at"]
    from choregos_adapters.github import RUNNER_PERMISSIONS

    assert github_app == [
        {"repo": "DiametralGroup/choregos-sandbox-dev", "permissions": RUNNER_PERMISSIONS, "ttl_s": 3600}
    ]
    assert RUNNER_PERMISSIONS == {"contents": "write", "metadata": "read"}, (
        "nothing more than cloning and pushing"
    )


async def test_without_an_app_or_outside_github_the_token_is_null(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHOREGOS_GITHUB_APP_ID", raising=False)
    run_id = await run_with_repository(project, "https://github.com/acme/public.git")
    body = (
        await client.post(f"/api/v1/internal/runs/{run_id}/git-token", headers=run_token_for(run_id))
    ).json()
    assert (body["token"], body["repository"]) == (None, "acme/public")

    elsewhere = await run_with_repository(project, "https://gitlab.com/acme/app.git")
    body = (
        await client.post(f"/api/v1/internal/runs/{elsewhere}/git-token", headers=run_token_for(elsewhere))
    ).json()
    assert (body["token"], body["repository"]) == (None, None)


async def test_a_finished_run_or_an_unknown_one_gets_nothing(
    client: AsyncClient, project: dict[str, Any], github_app: list[dict[str, Any]]
) -> None:
    finished = await run_with_repository(
        project, "https://github.com/acme/app.git", result={"status": "done", "summary": "x"}
    )
    assert (
        await client.post(f"/api/v1/internal/runs/{finished}/git-token", headers=run_token_for(finished))
    ).status_code == 409
    other = await run_with_repository(project, "https://github.com/acme/app.git")
    assert (await client.post(f"/api/v1/internal/runs/{other}/git-token")).status_code == 401
    assert (
        await client.post(f"/api/v1/internal/runs/{other}/git-token", headers=run_token_for(finished))
    ).status_code in {401, 403}
    assert github_app == [], "no token minted for a finished run, without a token, or in another's name"
