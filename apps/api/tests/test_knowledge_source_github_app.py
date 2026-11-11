"""A person may read a git source through the deployment's GitHub App (S51-06c, ADR 0117 amendment).

An organisation administrator, on a github.com repository whose owner the organisation claimed an
installation on. The sync mints a one-hour `contents: read` token through that installation.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import AsyncClient

from .github_app_kit import add_organisation, claim_for
from .test_knowledge_rights import PEOPLE, _admin_base, _as, _email, _uid, two_projects  # noqa: F401

KB = "/api/v1/orgs/varga/knowledge/fiches"
URL = "https://github.com/acme/docs.git"
ASKED = {"url": URL, "paths": ["docs/**/*.md"], "github_app": True}


@pytest.fixture
def minted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from choregos_adapters.github import GitHubAppAuth, InstallationToken
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "5242171")
    monkeypatch.setenv(
        "CHOREGOS_GITHUB_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"
    )
    reset_settings_cache()
    asked: list[dict[str, Any]] = []

    async def mint(self: Any, client: Any, repo: str, **options: Any) -> InstallationToken:
        asked.append({"repo": repo, "pinned": self.pinned_installation, **options})
        return InstallationToken(token="ghs_kb", expires_at=time.time() + 3600, repositories=(repo,))

    monkeypatch.setattr(GitHubAppAuth, "token_for", mint)
    return asked


async def _varga_claims(login: str, installation_id: int = 7) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
    await claim_for(installation_id, login, org_id)


async def _audit(client: AsyncClient, action: str) -> list[dict[str, Any]]:
    return [a for a in (await client.get("/api/v1/audit")).json()["items"] if a["action"] == action]


def _post(client: AsyncClient, config: dict[str, Any], **extra: Any) -> Any:
    return client.post(f"{KB}/sources", json={"kind": "git", "name": "acme docs", "config": config, **extra})


async def test_an_administrator_reads_a_repository_through_the_installation_the_organisation_claimed(
    client: AsyncClient, two_projects: dict[str, Any], minted: list[dict[str, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from choregos_adapters.documents.git import GitSource
    from choregos_api.db.models import KnowledgeSource
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge import sync

    await _admin_base(client, two_projects)
    await add_organisation("globex")  # two organisations: nothing is implicit
    await _varga_claims("acme")
    created = await _post(client, ASKED)
    assert created.status_code == 201, created.text
    assert created.json()["config"] == {
        "url": URL,
        "ref": "main",
        "paths": ["docs/**/*.md"],
        "github_app": True,
    }
    (entry,) = await _audit(client, "knowledge.source.create")
    assert entry["payload"]["github_app"] is True

    monkeypatch.setattr(sync, "fakes_enabled", lambda: False)
    async with session_scope(orgs="*") as session:
        row = await session.get(KnowledgeSource, created.json()["id"])
        opened = await sync.open_source(session, row)
    assert isinstance(opened, GitSource) and opened.host == "github.com"
    assert await opened._token() == "ghs_kb"
    assert minted == [{"repo": "acme/docs", "pinned": 7, "permissions": {"contents": "read"}}]


async def test_without_a_claimed_installation_the_request_is_refused_and_names_the_owner(
    client: AsyncClient, two_projects: dict[str, Any], minted: list[dict[str, Any]],  # noqa: F811
) -> None:  # fmt: skip
    await _admin_base(client, two_projects)
    globex = await add_organisation("globex")
    await claim_for(9, "acme", globex)  # another organisation's installation is no use
    refused = await _post(client, ASKED)
    assert refused.status_code == 409, refused.text
    assert "`acme`" in refused.text and "Settings, Connections" in refused.text
    listed = await client.get(f"{KB}")
    assert listed.json()["sources"] == []


async def test_a_single_organisation_instance_needs_no_claim_as_the_sync_does(
    client: AsyncClient, two_projects: dict[str, Any], minted: list[dict[str, Any]],  # noqa: F811
) -> None:  # fmt: skip
    await _admin_base(client, two_projects)
    created = await _post(client, ASKED)
    assert created.status_code == 201, created.text
    assert created.json()["config"]["github_app"] is True


async def test_a_non_administrator_may_not_ask_for_the_app_but_may_add_a_plain_source(
    client: AsyncClient, two_projects: dict[str, Any], minted: list[dict[str, Any]],  # noqa: F811
) -> None:  # fmt: skip
    await _admin_base(client, two_projects)
    await add_organisation("globex")
    await _varga_claims("acme")
    async with _as(client, _email("project owner"), **PEOPLE["project owner"]) as owner:
        refused = await _post(owner, ASKED)
        assert refused.status_code == 403, refused.text
        assert "administrator" in refused.text
        plain = await owner.post(
            f"{KB}/sources", json={"kind": "git", "name": "plain", "config": {"url": URL}}
        )
        assert plain.status_code == 201, plain.text
        assert "github_app" not in plain.json()["config"]


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (
            {"kind": "git", "config": {"url": "https://gitlab.com/acme/docs.git", "github_app": True}},
            "github.com",
        ),
        ({"kind": "web", "config": {"url": "https://example.org/", "github_app": True}}, "git source"),
        ({"kind": "git", "config": {"url": URL, "github_app": "yes"}}, "true or false"),
    ],
)
async def test_the_request_is_refused_where_the_app_cannot_serve(
    client: AsyncClient, two_projects: dict[str, Any], minted: list[dict[str, Any]],  # noqa: F811
    body: dict[str, Any], fragment: str,
) -> None:  # fmt: skip
    await _admin_base(client, two_projects)
    refused = await client.post(f"{KB}/sources", json={"name": "x", **body})
    assert refused.status_code == 422, refused.text
    assert fragment in refused.text


async def test_a_patch_keeps_the_app_while_the_url_stays_and_false_removes_it(
    client: AsyncClient, two_projects: dict[str, Any], minted: list[dict[str, Any]],  # noqa: F811
) -> None:  # fmt: skip
    await _admin_base(client, two_projects)
    await add_organisation("globex")
    await _varga_claims("acme")
    source = (await _post(client, ASKED)).json()
    path = f"{KB}/sources/{source['id']}"

    async with _as(client, _email("project owner"), **PEOPLE["project owner"]) as owner:
        edited = await owner.patch(path, json={"config": {"url": URL, "ref": "dev"}})
        assert edited.status_code == 200, edited.text
        assert edited.json()["config"]["github_app"] is True, "an editor keeps what the source had"
        elsewhere = await owner.patch(path, json={"config": {"url": "https://github.com/acme/other.git"}})
        assert elsewhere.status_code == 200
        assert "github_app" not in elsewhere.json()["config"], "another URL is another decision"
        denied = await owner.patch(path, json={"config": {"url": URL, "github_app": True}})
        assert denied.status_code == 403, denied.text

    again = await client.patch(path, json={"config": {"url": URL, "github_app": True}})
    assert again.status_code == 200 and again.json()["config"]["github_app"] is True
    removed = await client.patch(path, json={"config": {"url": URL, "github_app": False}})
    assert removed.status_code == 200, removed.text
    assert "github_app" not in removed.json()["config"]
    changes = [e["payload"]["github_app"] for e in await _audit(client, "knowledge.source.github_app")]
    assert sorted(changes) == [False, False, True], "another URL, the administrator's yes, then their no"
