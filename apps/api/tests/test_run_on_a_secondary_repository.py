"""A run on a work item's target repository gets that repository's host and token (S58-02, ADR 0124).

The orchestrator records the target in the run's `StageInput.repo`; the egress proxy's list and the
runner's git token are derived from it, never from the project's primary repository.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import AsyncClient

from .github_app_kit import add_organisation, claim_for
from .kits.run_git_token import run_token_for, run_with_repository
from .test_network_policy_api import _policy, _run

DIAMCTL = {
    "url": "https://git.acme.example/varga/diamctl.git",
    "base_branch": "trunk",
    "work_branch": "fix/1",
}


async def test_the_proxy_opens_the_target_s_host_and_not_the_primary_s(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await client.put(f"/api/v1/projects/{project['id']}/policy", json={"yaml": _policy(["pypi.org"])})
    headers = await _run(project, "run-diamctl", stage_input={"repo": DIAMCTL})
    body = (await client.get("/api/v1/internal/runs/run-diamctl/network", headers=headers)).json()
    assert body["implied"] == ["git.acme.example"], "the repository it cloned, not github.com (the primary)"
    assert body["allow"] == ["pypi.org"]


@pytest.fixture
def minted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from choregos_adapters.github import GitHubAppAuth, InstallationToken

    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "5242171")
    monkeypatch.setenv(
        "CHOREGOS_GITHUB_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"
    )
    asked: list[dict[str, Any]] = []

    async def mint(self: Any, client: Any, repo: str, **options: Any) -> InstallationToken:
        asked.append({"repo": repo, "pinned": self.pinned_installation, "account": self.pinned_account})
        return InstallationToken(token="ghs_run", expires_at=time.time() + 3600, repositories=(repo,))

    monkeypatch.setattr(GitHubAppAuth, "token_for", mint)
    return asked


async def test_the_git_token_of_a_run_on_a_secondary_repository_is_that_repository_s(
    client: AsyncClient, project: dict[str, Any], minted: list[dict[str, Any]]
) -> None:
    """The project's primary is `varga/billing-api`; the run cloned `acme-tools/diamctl`, whose owner
    the organisation claimed through another installation: the token is minted there, for it only."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await add_organisation("globex")
    async with session_scope(orgs="*") as session:
        varga = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
    await claim_for(5, "varga", varga)
    await claim_for(77, "acme-tools", varga)
    run_id = await run_with_repository(project, "https://github.com/acme-tools/diamctl.git")
    reply = await client.post(f"/api/v1/internal/runs/{run_id}/git-token", headers=run_token_for(run_id))
    assert reply.status_code == 200, reply.text
    assert reply.json()["repository"] == "acme-tools/diamctl"
    assert minted == [{"repo": "acme-tools/diamctl", "pinned": 77, "account": "acme-tools"}]
