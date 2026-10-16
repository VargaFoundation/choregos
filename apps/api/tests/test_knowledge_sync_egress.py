"""The worker's knowledge sync reads the `workers` family of the egress policy (S39-22b, ADR 0066)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import AsyncClient

BASE = "/api/v1/orgs/varga/knowledge"


async def _public(host: str) -> list[str]:
    return ["93.184.216.34"]


@pytest.fixture
def crawler(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """`open_source` hands the sync a real crawler over a recording transport."""
    from choregos_adapters.documents import WebCrawlSource

    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(404)

    async def open_source(session: Any, source: Any) -> Any:
        return WebCrawlSource(
            url="https://docs.example.test/guide/", transport=httpx.MockTransport(answer), resolver=_public
        )

    monkeypatch.setattr("choregos_api.knowledge.sync.open_source", open_source)
    return sent


async def _source(client: AsyncClient) -> str:
    await client.post(BASE, json={"slug": "docs", "name": "Docs", "serving_mode": "index"})
    created = await client.post(
        f"{BASE}/docs/sources",
        json={
            "kind": "web",
            "name": "Site",
            "config": {"url": "https://docs.example.test/guide/"},
            "allow_egress": True,
        },
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def test_the_activity_syncs_as_the_workers_family(
    client: AsyncClient, admin: str, app: Any, monkeypatch: pytest.MonkeyPatch, crawler: list[httpx.Request]
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.knowledge.sync import sync_source
    from choregos_orchestrator.activities.knowledge import sync_knowledge_source

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()
    source_id = await _source(client)
    payload = {"org": "varga", "source_id": source_id}
    # the API family is declared, the workers' is not: it is unknown, so only the org list governs
    monkeypatch.setenv("CHOREGOS_EGRESS_CEILING", '{"api": ["elsewhere.example.test"]}')
    reset_settings_cache()
    assert (await sync_knowledge_source(payload))["status"] == "synced"
    assert crawler, "the crawler reached the site"
    crawler.clear()
    # the workers' family is declared and excludes the host: refused before any request
    monkeypatch.setenv("CHOREGOS_EGRESS_CEILING", '{"workers": ["elsewhere.example.test"]}')
    reset_settings_cache()
    with pytest.raises(Exception, match="outside the platform's declared network for the workers"):
        await sync_source("varga", source_id)  # what the activity runs on its next due pass
    assert crawler == [], "ceiling_outside: nothing was sent"
