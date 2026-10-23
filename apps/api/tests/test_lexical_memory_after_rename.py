"""The lexical memory finds a renamed organisation by its memory key (S40-24)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core.domain import Fact
from httpx import AsyncClient

from .agent_scopes_kit import ORG
from .test_renommer_une_organisation import _declarer_administrateur

pytestmark = pytest.mark.usefixtures("real_adapters")


@pytest.fixture
def real_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "0")


def _memory() -> Any:
    from choregos_adapters import build

    return build("memory", "lexical", {"org": "*"})


async def test_the_old_slug_still_names_the_organisation_after_a_rename(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.edition import reinitialiser

    await client.post(f"{ORG}/memories", json={"slug": "lessons", "display_name": "L"})
    declared = await client.post(
        f"{ORG}/connectors", json={"name": "memory-org", "type": "lexical", "kind": "memory"}
    )
    assert declared.status_code == 201, declared.text
    memory = _memory()
    namespace = "org:varga#memory:lessons"
    await memory.write_fact(namespace, Fact(kind="decision", subject="tls", content="Pin TLS."))
    await _declarer_administrateur({"admin@varga.dev"})
    try:
        renamed = await client.patch("/api/v1/orgs/varga", json={"slug": "diametral"})
        assert renamed.status_code == 200, renamed.text
        assert [m.content for m in await memory.search(namespace, "TLS", k=5)] == ["Pin TLS."]
        await memory.write_fact(namespace, Fact(kind="decision", subject="hsts", content="Send HSTS."))
        found = await client.get("/api/v1/orgs/diametral/memories/lessons/search", params={"q": "HSTS"})
        assert [m["subject"] for m in found.json()] == ["hsts"]
    finally:
        reinitialiser()
