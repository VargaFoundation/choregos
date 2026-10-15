"""Knowledge sources (S33-04, ADR 0051): synced from SharePoint/OneDrive, a web site or git; a document
deleted at the source leaves the base and its index."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from . import knowledge_fixtures as fx

BASE = "/api/v1/orgs/varga/knowledge"


async def _sharepoint(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, mode: str = "index"
) -> dict[str, Any]:
    """An `index` base, a `documents` connector (Sites.Selected app), and a SharePoint source."""
    monkeypatch.setenv("CONNECTOR_KB_GRAPH_SECRET", "s3cret")
    created = await client.post(BASE, json={"slug": "framework", "name": "Framework", "serving_mode": mode})
    assert created.status_code == 201, created.text
    connector = await client.post(
        "/api/v1/orgs/varga/connectors",
        json={
            "name": "sharepoint",
            "kind": "documents",
            "type": "sharepoint",
            "config": {"tenant_id": "acme", "client_id": "choregos-kb"},
            "secret_refs": {"client_secret": "env:CONNECTOR_KB_GRAPH_SECRET"},
        },
    )
    assert connector.status_code == 201, connector.text
    source = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "sharepoint", "name": "Architecture site", "connector": "sharepoint",
              "config": {"site_id": "acme.sharepoint.com,1,2", "folder": "Policies"}, "interval_minutes": 30},
    )  # fmt: skip
    assert source.status_code == 201, source.text
    return dict(source.json())


async def _sync(source_id: str) -> dict[str, Any]:
    from choregos_api.knowledge.sync import sync_source

    return await sync_source("varga", source_id)


async def _search(client: AsyncClient, query: str) -> set[str]:
    found = await client.post(f"{BASE}/framework/search", json={"query": query})
    assert found.status_code == 200, found.text
    return {hit["filename"] for hit in found.json()["hits"]}


async def _chunks_of(document_id: str) -> int:
    from choregos_api.db.models import KnowledgeChunk
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    async with session_scope() as session:
        return int(
            (
                await session.execute(select(func.count()).where(KnowledgeChunk.document_id == document_id))
            ).scalar_one()
        )


async def test_a_document_deleted_at_the_source_leaves_the_index(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The acceptance of S33-04."""
    from choregos_adapters.fakes.documents import fake_source

    source = await _sharepoint(client, monkeypatch)
    site = fake_source(source["id"])
    site.put("item-zoning", "zoning.pptx", fx.pptx([("Network zoning", "Every zone has its own firewall")]))
    site.put("item-tls", "encryption.md", b"# Encryption\n\n## In transit\n\nTLS 1.3 is required.\n")
    site.put("item-logo", "logo.png", b"\x89PNG")

    first = await _sync(source["id"])
    assert (first["added"], first["skipped"]) == (2, 1), first
    base = (await client.get(f"{BASE}/framework")).json()
    zoning = next(d for d in base["documents"] if d["filename"] == "zoning.pptx")
    assert zoning["status"] == "ready" and zoning["source_id"] == source["id"]
    assert await _search(client, "firewall zone") >= {"zoning.pptx"}
    assert await _chunks_of(zoning["id"]) > 0

    site.delete("item-zoning")
    second = await _sync(source["id"])
    assert second["deleted"] == 1, second
    assert "zoning.pptx" not in await _search(client, "firewall zone"), "gone from the index"
    assert await _chunks_of(zoning["id"]) == 0
    assert (await client.get(f"{BASE}/framework/documents/{zoning['id']}")).status_code == 404

    read = (await client.get(f"{BASE}/framework")).json()
    (state,) = read["sources"]
    assert state["status"] == "idle" and state["last_synced_at"] and state["document_count"] == 1
    assert state["last_summary"]["deleted"] == 1


async def test_a_sync_replayed_changes_nothing_twice(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0008: the cursor moves only once the changes are applied; replaying them is a no-op."""
    from choregos_adapters.fakes.documents import fake_source
    from choregos_api.db.models import KnowledgeSource
    from choregos_api.db.session import session_scope

    source = await _sharepoint(client, monkeypatch, mode="files")
    site = fake_source(source["id"])
    site.put("a", "a.md", b"# A\n")
    await _sync(source["id"])
    async with session_scope() as session:
        row = await session.get(KnowledgeSource, source["id"])
        assert row is not None
        row.cursor = None  # as if the cursor had not been saved: the whole delta comes again
    fetched = len(site.fetched)
    again = await _sync(source["id"])
    assert (again["added"], again["updated"], again["unchanged"]) == (0, 0, 1)
    assert len(site.fetched) == fetched, "a version already stored is not fetched again"

    site.put("a", "a.md", b"# A, revised\n")
    changed = await _sync(source["id"])
    assert changed["updated"] == 1
    base = (await client.get(f"{BASE}/framework")).json()
    (document,) = base["documents"]
    read = (await client.get(f"{BASE}/framework/documents/{document['id']}")).json()
    assert read["markdown"].startswith("# A, revised")


async def test_a_failed_sync_says_why_and_keeps_its_cursor(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.documents.base import Changes
    from choregos_api.db.models import KnowledgeSource
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.sync import sync_source

    source = await _sharepoint(client, monkeypatch, mode="files")

    class Broken:
        async def changes(self, cursor: Any) -> Changes:
            raise RuntimeError("graph unreachable")

        async def fetch(self, item: Any) -> bytes:
            raise AssertionError

    with pytest.raises(RuntimeError):
        await sync_source("varga", source["id"], adapter=Broken())
    async with session_scope() as session:
        row = await session.get(KnowledgeSource, source["id"])
        assert row is not None
        assert (row.status, row.cursor) == ("failed", None)
        assert "graph unreachable" in (row.last_error or "")


async def test_a_full_listing_deletes_what_it_does_not_list_unless_a_page_was_unreadable() -> None:
    from types import SimpleNamespace

    from choregos_adapters.documents.base import Changes, SourceItem
    from choregos_api.knowledge.sync import _deleted

    known: dict[str, Any] = {"a": SimpleNamespace(), "b": SimpleNamespace()}
    listed = [SourceItem(ref="a", name="a.md", version="1")]
    assert await _deleted(Changes(items=listed, full=True), known) == {"b"}
    assert await _deleted(Changes(items=listed, full=True, unreadable=["c"]), known) == set()
    assert await _deleted(Changes(items=[], deleted=["b", "z"]), known) == {"b"}


async def test_the_loop_starts_with_the_source_and_syncs_on_demand(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch, app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    source = await _sharepoint(client, monkeypatch, mode="files")
    temporal: Any = get_temporal()
    loop = f"knowledge-sync-{source['id']}"
    assert temporal.started[loop] == {"org": "varga", "source_id": source["id"], "interval_seconds": 1800}
    assert (await client.post(f"{BASE}/framework/sources/{source['id']}/sync")).status_code == 202
    assert (loop, "sync_now", {}) in temporal.signals
    assert (await client.delete(f"{BASE}/framework/sources/{source['id']}")).status_code == 204
    assert (loop, "stop", {}) in temporal.signals
    assert (await client.get(f"{BASE}/framework")).json()["sources"] == []


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"kind": "web", "name": "w", "config": {"url": "http://docs.example.com/"}}, "https"),
        (
            {
                "kind": "git",
                "name": "g",
                "config": {"url": "https://git.test/r.git", "ref": "--upload-pack=x"},
            },
            "ref",
        ),
        ({"kind": "sharepoint", "name": "s", "config": {"site_id": "x"}}, "connector"),
        (
            {"kind": "sharepoint", "name": "s", "connector": "nope", "config": {"site_id": "x"}},
            "no `documents`",
        ),
    ],
)
async def test_a_source_is_refused_before_its_first_sync(
    client: AsyncClient, admin: str, body: dict[str, Any], reason: str
) -> None:
    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    refused = await client.post(f"{BASE}/framework/sources", json=body)
    assert refused.status_code == 422, refused.text
    assert reason in refused.json()["detail"]


async def _declare(client: AsyncClient, name: str, kind: str, type_: str) -> None:
    created = await client.post(
        "/api/v1/orgs/varga/connectors", json={"name": name, "kind": kind, "type": type_, "config": {}}
    )
    assert created.status_code == 201, created.text


async def _source_refused(client: AsyncClient, connector: str) -> str:
    refused = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "sharepoint", "name": "s", "connector": connector, "config": {"site_id": "x"}},
    )
    assert refused.status_code == 422, refused.text
    return str(refused.json()["detail"])


async def test_a_sharepoint_source_names_an_existing_documents_connector(
    client: AsyncClient, admin: str
) -> None:
    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    # No connector at all: the message says who declares one.
    assert "none: an org admin declares one in /admin/connectors" in await _source_refused(
        client, "Diametral"
    )
    # An `identity` connector of that name is named for what it is.
    await _declare(client, "diametral", "identity", "fake")
    assert "`diametral` is a identity/fake connector, not a documents one" in await _source_refused(
        client, "diametral"
    )
    # A documents connector under another name: the message lists the ones that exist.
    await _declare(client, "wiki", "documents", "fake")
    assert "documents connectors here: wiki" in await _source_refused(client, "other")
    # Names are lowercase: the capitalised name is not found, and the message says what to type.
    await _declare(client, "sharepoint", "documents", "sharepoint")
    assert 'did you mean "sharepoint"?' in await _source_refused(client, "Sharepoint")


async def test_a_sharepoint_source_refuses_a_connector_of_another_type(
    client: AsyncClient, admin: str
) -> None:
    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    await _declare(client, "wiki", "documents", "fake")
    reason = await _source_refused(client, "wiki")
    assert "`wiki` is a documents/fake connector: a sharepoint source needs sharepoint" in reason


async def test_a_sync_refuses_a_connector_of_another_type(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same check at sync time: a connector whose type changed after the source was declared."""
    from choregos_api.db.models import KnowledgeSource, OrgConnector
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.sync import SourceUnavailableError, open_source
    from sqlalchemy import select

    source = await _sharepoint(client, monkeypatch)
    async with session_scope() as session:
        connector = (
            await session.execute(select(OrgConnector).where(OrgConnector.name == "sharepoint"))
        ).scalar_one()
        connector.type = "fake"
    async with session_scope() as session:
        row = await session.get(KnowledgeSource, source["id"])
        assert row is not None
        with pytest.raises(SourceUnavailableError, match="documents/fake connector"):
            await open_source(session, row)


async def test_web_and_git_sources_sync_through_their_fakes(client: AsyncClient, admin: str) -> None:
    from choregos_adapters.fakes.documents import fake_source

    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    for body, name, content in (
        ({"kind": "web", "name": "Runbooks",
          "config": {"url": "https://docs.example.com/", "max_pages": 900}},
         "restart.html", b"<html><body><h1>Restart</h1></body></html>"),
        ({"kind": "git", "name": "Platform docs", "config": {"url": "https://git.example.com/p.git"}},
         "adr.md", b"# ADR\n"),
    ):  # fmt: skip
        created = await client.post(f"{BASE}/framework/sources", json=body)
        assert created.status_code == 201, created.text
        if body["kind"] == "web":
            assert created.json()["config"]["max_pages"] == 500, "capped"
        fake_source(created.json()["id"]).put(name, name, content)
        assert (await _sync(created.json()["id"]))["added"] == 1
    titles = {d["title"] for d in (await client.get(f"{BASE}/framework")).json()["documents"]}
    assert titles == {"Restart", "ADR"}


async def test_the_activity_skips_a_pass_that_is_not_due(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S39-19: a restarted worker or a `continue_as_new` pass must not sync off-schedule."""
    from datetime import UTC, datetime, timedelta

    from choregos_adapters.fakes.documents import fake_source
    from choregos_api.knowledge import refresh
    from choregos_orchestrator.activities.knowledge import sync_knowledge_source

    now = [datetime(2026, 10, 16, 8, 0, tzinfo=UTC)]
    monkeypatch.setattr(refresh, "utcnow", lambda: now[0])
    await client.post(BASE, json={"slug": "framework", "name": "Framework", "serving_mode": "files"})
    created = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "git", "name": "Docs", "config": {"url": "https://git.example.com/p.git"}},
    )
    source_id = created.json()["id"]
    fake_source(source_id).put("a", "a.md", b"# A\n")
    payload = {"org": "varga", "source_id": source_id}

    first = await sync_knowledge_source(payload)
    assert first["added"] == 1
    assert first["interval_seconds"] == 3600
    shown = (await client.get(f"{BASE}/framework")).json()["sources"][0]
    assert datetime.fromisoformat(shown["next_sync_at"]).replace(tzinfo=UTC) == now[0] + timedelta(minutes=60)

    fetched = len(fake_source(source_id).fetched)
    now[0] += timedelta(minutes=10)
    second = await sync_knowledge_source(payload)
    assert second == {"status": "not_due", "interval_seconds": 3000}
    assert len(fake_source(source_id).fetched) == fetched

    now[0] += timedelta(minutes=51)
    third = await sync_knowledge_source(payload)
    assert third["status"] == "synced"
