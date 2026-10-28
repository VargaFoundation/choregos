# SPDX-License-Identifier: Apache-2.0
"""`GET /procedures`: the setup procedures, with this deployment's URLs filled in (ADR 0067)."""

from __future__ import annotations

import pathlib
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_api.config import reset_settings_cache
from choregos_core import procedures as core
from httpx import AsyncClient

# http, not https: an https URL makes the session cookie `Secure` and the test client drops it
API = "http://agentic.example.test"
CONSOLE = "http://console.example.test"


@pytest.fixture(autouse=True)
def deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", CONSOLE)
    monkeypatch.delenv("CHOREGOS_PROCEDURES_EXTRA", raising=False)
    reset_settings_cache()
    core.reload()
    yield
    reset_settings_cache()
    core.reload()


async def test_it_needs_a_signed_in_member(client: AsyncClient, org: str) -> None:
    assert (await client.get("/api/v1/procedures")).status_code == 401
    assert (await client.get("/api/v1/procedures/connector.tracker.jira")).status_code == 401


async def test_the_index_lists_every_procedure_that_ships(client: AsyncClient, admin: str) -> None:
    body = (await client.get("/api/v1/procedures")).json()
    ids = [p["id"] for p in body]
    assert ids == sorted(ids)
    assert set(ids) == set(core.load().procedures)
    assert len(ids) >= 48
    first = body[0]
    assert set(first) == {"id", "title", "version", "audience", "applies_to", "link"}


async def test_the_detail_gives_this_deployments_webhook_url(client: AsyncClient, admin: str) -> None:
    response = await client.get("/api/v1/procedures/connector.tracker.jira")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["values"] == {"webhook_url:jira": f"{API}/api/v1/webhooks/jira"}
    text = "\n".join(s["markdown"] for s in body["sections"])
    assert f"{API}/api/v1/webhooks/jira" in text
    assert "{{" not in text
    assert [s["key"] for s in body["sections"]] == [
        "before",
        "at",
        "permissions",
        "in_choregos",
        "test",
        "fails",
    ]
    assert body["origin"] == "shipped" and body["reviewed"] == "2026-10-28"
    assert body["vendor_docs"] and all(d["url"].startswith("https://") for d in body["vendor_docs"])
    assert body["fields"]["project_key"]


async def test_the_public_url_fills_the_door_and_slack_procedures(client: AsyncClient, admin: str) -> None:
    door = (await client.get("/api/v1/procedures/agent.door.cursor")).json()
    assert f"{CONSOLE}/mcp" in door["sections"][0]["markdown"]
    assert door["link"] == "/integrations/cursor"
    slack = (await client.get("/api/v1/procedures/connector.notify.slack")).json()
    assert slack["values"] == {"public_url": CONSOLE}


async def test_an_unknown_procedure_is_a_404(client: AsyncClient, admin: str) -> None:
    assert (await client.get("/api/v1/procedures/connector.nothing.here")).status_code == 404


async def test_for_gives_the_one_best_procedure(client: AsyncClient, admin: str) -> None:
    async def best(**params: str) -> list[str]:
        response = await client.get("/api/v1/procedures", params=params)
        assert response.status_code == 200, response.text
        return [p["id"] for p in response.json()]

    assert await best(**{"for": "connector:documents/sharepoint"}) == ["connector.documents.sharepoint"]
    assert await best(**{"for": "source:web"}) == ["source.web"]
    assert await best(**{"for": "door_client:cursor"}) == ["agent.door.cursor"]
    # No preset: the generic MCP procedure. With the Rovo preset: Atlassian's.
    assert await best(**{"for": "connector:mcp/mcp"}) == ["connector.mcp.mcp"]
    assert await best(**{"for": "connector:mcp/mcp", "preset": "atlassian-rovo"}) == [
        "connector.mcp.mcp.atlassian-rovo"
    ]
    assert await best(**{"for": "connector:nothing/here"}) == []


async def test_a_deployment_adds_notes_and_procedures_through_its_directory(
    client: AsyncClient, admin: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "connector.documents.sharepoint.site.md").write_text(
        "## On this platform\n\nEntra apps are registered by the platform team.\n", encoding="utf-8"
    )
    extra = (core.SHIPPED / "connector.notify.slack.md").read_text(encoding="utf-8")
    (tmp_path / "connector.notify.teams.md").write_text(
        extra.replace("connector.notify.slack", "connector.notify.teams").replace(
            "notify/slack", "notify/teams"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CHOREGOS_PROCEDURES_EXTRA", str(tmp_path))
    reset_settings_cache()

    sharepoint = (await client.get("/api/v1/procedures/connector.documents.sharepoint")).json()
    assert sharepoint["sections"][-1] == {
        "key": "site",
        "title": "On this platform",
        "markdown": "Entra apps are registered by the platform team.",
    }
    teams = (await client.get("/api/v1/procedures/connector.notify.teams")).json()
    assert teams["origin"] == "extra"
    found = (await client.get("/api/v1/procedures", params={"for": "connector:notify/teams"})).json()
    assert [p["id"] for p in found] == ["connector.notify.teams"]
    # Without the directory the notes are gone: they are the deployment's, not the platform's.
    monkeypatch.delenv("CHOREGOS_PROCEDURES_EXTRA")
    reset_settings_cache()
    plain = (await client.get("/api/v1/procedures/connector.documents.sharepoint")).json()
    assert plain["sections"][-1]["key"] == "fails"
