"""The platform's outbound clients refuse a host the organisation has not opened — before sending
(S39-22b, ADR 0066). Every test here fails if its guard is removed: the recorders see the call."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest
from httpx import AsyncClient

from .egress_support import ORG, rules_in_db
from .test_courtier import OUTILS, _preparer
from .test_skills_from_git import ANTHROPICS, REPO_V1, SHA1, tarball

TOOLS = [{"name": "order_status", "inputSchema": {"type": "object"}}]


@pytest.fixture
def enforce(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(outils=[dict(t) for t in TOOLS])
    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", server)
    yield server


async def _revoke(client: AsyncClient, host: str) -> None:
    rule = next(r for r in await rules_in_db("allowed") if r.host == host)
    assert (await client.delete(f"{ORG}/egress/rules/{rule.id}")).status_code == 200


async def test_discovery_on_a_revoked_host_is_a_connector_error_and_the_vendor_sees_nothing(
    client: AsyncClient, admin: str, enforce: None, vendor: Any
) -> None:
    body = {"name": "vendor", "type": "mcp", "config": {"url": "https://vendor.test/mcp"}}
    assert (await client.post(f"{ORG}/connectors", json={**body, "allow_egress": True})).status_code == 201
    assert (await client.post(f"{ORG}/connectors/vendor/discover")).status_code == 200
    reached = len(vendor.recues)
    assert reached > 0
    await _revoke(client, "vendor.test")
    refused = await client.post(f"{ORG}/connectors/vendor/discover")
    assert refused.status_code == 502 and "egress refused" in refused.text, refused.text
    assert "vendor.test" in refused.text and "/admin/egress" in refused.text
    assert len(vendor.recues) == reached, "the refused call never left"
    connector = (await client.get(f"{ORG}/connectors/vendor")).json()
    assert connector["status"] == "error" and "egress refused" in connector["last_error"]


async def test_a_broker_call_during_a_run_on_a_revoked_host_is_a_tool_error_not_a_timeout(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    enforce: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(outils=[dict(o) for o in OUTILS])
    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_CLE_FOURNISSEUR", "x")
    # open the host first (the setup declares and discovers under enforce)
    assert (
        await client.post(f"{ORG}/egress/rules", json={"host": "fournisseur.test", "used_by": ["api"]})
    ).status_code == 201
    base, headers = await _preparer(client, project, ["*"])
    ok = await client.post(f"{base}/fournisseur__stock", headers=headers, json={})
    assert ok.json()["status_code"] == 200, ok.text
    reached = len(server.recues)
    await _revoke(client, "fournisseur.test")
    refused = await client.post(f"{base}/fournisseur__stock", headers=headers, json={})
    assert refused.json()["status_code"] == 422 and "egress refused" in refused.text, refused.text
    assert len(server.recues) == reached, "nothing left for the vendor"


@pytest.fixture
def github(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """GitHub, recorded: the HTTPS fetcher through a `MockTransport`."""
    from choregos_api.services.skill_sources import HttpsSourceFetcher, set_fetcher

    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(200, text=SHA1)
        return httpx.Response(200, content=tarball(REPO_V1))

    set_fetcher(HttpsSourceFetcher(transport=httpx.MockTransport(answer)))
    yield seen
    set_fetcher(None)


def _import_body(**extra: Any) -> dict[str, Any]:
    return {"repo_url": ANTHROPICS, "sha": SHA1, "path": "skills", "skills": ["onboarding"], **extra}


async def test_a_git_import_outside_the_deployment_ceiling_is_a_422_that_names_egress(
    client: AsyncClient,
    admin: str,
    enforce: None,
    github: list[httpx.Request],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_CEILING", '{"api": ["api.github.com"]}')
    reset_settings_cache()
    refused = await client.post(f"{ORG}/skills/import-git", json=_import_body(allow_egress=True))
    assert refused.status_code == 422, refused.text
    assert "codeload.github.com" in refused.text and "egress refused" in refused.text
    assert [r.url.host for r in github] == [], "the tarball host was refused before any request"
    monkeypatch.delenv("CHOREGOS_EGRESS_CEILING")
    reset_settings_cache()
    assert await rules_in_db() == [], "the refused import took its rules with it: one transaction"
    done = await client.post(f"{ORG}/skills/import-git", json=_import_body(allow_egress=True))
    assert done.status_code == 201, done.text
    assert [r.url.host for r in github] == ["codeload.github.com"]


async def test_a_git_preview_with_a_revoked_api_host_is_refused_with_no_request(
    client: AsyncClient, admin: str, enforce: None, github: list[httpx.Request]
) -> None:
    request = {"repo_url": ANTHROPICS, "ref": "main", "path": "skills", "allow_egress": True}
    assert (await client.post(f"{ORG}/skills/import-git/preview", json=request)).status_code == 200
    await _revoke(client, "api.github.com")
    github.clear()
    gated = await client.post(f"{ORG}/skills/import-git/preview", json={**request, "allow_egress": False})
    assert gated.status_code == 409 and gated.json()["code"] == "egress_required"
    assert github == []


async def test_a_web_sync_with_the_host_absent_fails_the_source_and_sends_nothing(
    client: AsyncClient, admin: str, enforce: None
) -> None:
    from choregos_adapters.documents import WebCrawlSource
    from choregos_api.knowledge.sync import sync_source

    base = "/api/v1/orgs/varga/knowledge"
    assert (
        await client.post(base, json={"slug": "docs", "name": "Docs", "serving_mode": "index"})
    ).status_code == 201
    created = await client.post(
        f"{base}/docs/sources",
        json={
            "kind": "web",
            "name": "Site",
            "config": {"url": "https://docs.example.test/guide/"},
            "allow_egress": True,
        },
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(404)

    def crawler() -> WebCrawlSource:
        return WebCrawlSource(
            url="https://docs.example.test/guide/",
            transport=httpx.MockTransport(answer),
            resolver=_public,
        )

    await sync_source("varga", source_id, adapter=crawler())
    assert sent, "while the host is open, the crawler reaches it"
    await _revoke(client, "docs.example.test")
    sent.clear()
    with pytest.raises(Exception, match="egress refused"):
        await sync_source("varga", source_id, adapter=crawler())
    assert sent == [], "no HTTP request was made"
    source = (await client.get(f"{base}/docs")).json()["sources"][0]
    assert source["status"] == "failed" and "egress refused" in source["last_error"]


async def _public(host: str) -> list[str]:
    return ["93.184.216.34"]


async def test_a_remote_agents_card_fetch_outside_the_ceiling_is_refused_and_never_sent(
    client: AsyncClient, admin: str, enforce: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.fakes.a2a import FakeA2aAgent

    from .conftest_remote import remote_agent, set_sealing_key

    set_sealing_key(monkeypatch)
    for remote in remote_agent():
        assert isinstance(remote, FakeA2aAgent)
        await client.post(f"{ORG}/remote-credentials", json={"name": "rovo", "type": "none"})
        remote.security = "none"
        host = urlsplit(remote.card_url).hostname or ""
        await client.post(f"{ORG}/egress/rules", json={"host": host, "used_by": ["api", "runners"]})
        discover = {"card_url": remote.card_url}
        assert (await client.post(f"{ORG}/agents/discover", json=discover)).status_code == 200
        fetched = remote.card_fetches
        # the organisation still allows it, the deployment's ceiling no longer does: the gate passes,
        # the guard refuses before the card is requested
        from choregos_api.config import reset_settings_cache

        monkeypatch.setenv("CHOREGOS_EGRESS_CEILING", '{"api": ["elsewhere.example.test"]}')
        reset_settings_cache()
        refused = await client.post(f"{ORG}/agents/discover", json=discover)
        assert refused.status_code == 422 and "egress refused" in refused.text, refused.text
        assert remote.card_fetches == fetched, "the card was never requested"
