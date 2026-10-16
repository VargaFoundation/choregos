"""The runner's git token comes from the installation the run's organisation claimed (ADR 0073).

Discovery by repository handed a token to any organisation that typed the repository of another
one that had installed the App: latent in the community edition, real with several organisations.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import AsyncClient

from .github_app_kit import add_organisation, claim_for
from .test_jeton_git_du_run import _jeton_de_run, _run_avec_depot


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


async def _token(client: AsyncClient, project: dict[str, Any], url: str) -> dict[str, Any]:
    run_id = await _run_avec_depot(project, url)
    reply = await client.post(f"/api/v1/internal/runs/{run_id}/git-token", headers=_jeton_de_run(run_id))
    assert reply.status_code == 200, reply.text
    return dict(reply.json())


async def test_in_a_two_org_instance_a_run_gets_no_token_for_another_orgs_repository(
    client: AsyncClient, project: dict[str, Any], minted: list[dict[str, Any]]
) -> None:
    globex = await add_organisation("globex")
    await claim_for(1, "globex-inc", globex)
    reply = await _token(client, project, "https://github.com/globex-inc/private-repo.git")
    assert reply["token"] is None, "the installation belongs to globex, the run is varga's"
    assert minted == [], "nothing was minted"


async def test_a_run_is_pinned_to_the_installation_its_organisation_claimed(
    client: AsyncClient, project: dict[str, Any], minted: list[dict[str, Any]]
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await add_organisation("globex")
    async with session_scope(orgs="*") as session:
        varga = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
    await claim_for(5, "varga-inc", varga)
    reply = await _token(client, project, "https://github.com/varga-inc/billing.git")
    assert reply["token"] == "ghs_run"
    assert minted == [{"repo": "varga-inc/billing", "pinned": 5, "account": "varga-inc"}]


async def test_a_single_org_instance_still_discovers_an_unclaimed_repository(
    client: AsyncClient, project: dict[str, Any], minted: list[dict[str, Any]]
) -> None:
    reply = await _token(client, project, "https://github.com/anyone/app.git")
    assert reply["token"] == "ghs_run"
    assert minted == [{"repo": "anyone/app", "pinned": None, "account": None}], "today's behaviour"
