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
    monkeypatch.setenv("KB_GRAPH_SECRET", "s3cret")
    created = await client.post(BASE, json={"slug": "framework", "name": "Framework", "serving_mode": mode})
    assert created.status_code == 201, created.text
    connector = await client.post(
        "/api/v1/orgs/varga/connectors",
        json={
            "name": "sharepoint",
            "kind": "documents",
            "type": "sharepoint",
            "config": {"tenant_id": "acme", "client_id": "choregos-kb"},
            "secret_refs": {"client_secret": "env:KB_GRAPH_SECRET"},
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


async def test_an_unknown_source_kind_is_unavailable_not_faked(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from choregos_api.knowledge.sync import SourceUnavailableError, open_source

    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    from choregos_adapters import registry

    monkeypatch.setattr(registry, "fakes_enabled", lambda: False)
    monkeypatch.setattr("choregos_api.knowledge.sync.fakes_enabled", lambda: False)
    source = SimpleNamespace(kind="carrier-pigeon", config={}, connector=None, org_id="o", id="s")
    with pytest.raises(SourceUnavailableError, match="carrier-pigeon"):
        await open_source(None, source)  # type: ignore[arg-type]


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


async def _confluence(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, site: Any, **config: Any
) -> dict[str, Any]:
    """An `index` base, a `documents/confluence` connector, and a source on the fake site, with the
    real adapter (fakes off) talking to `FakeConfluence` through the module's transport seam."""
    from choregos_adapters.documents import confluence

    monkeypatch.setenv("KB_CONFLUENCE_TOKEN", site.token)
    created = await client.post(
        BASE, json={"slug": "framework", "name": "Framework", "serving_mode": "index"}
    )
    assert created.status_code == 201, created.text
    declared = await client.post(
        "/api/v1/orgs/varga/connectors",
        json={
            "name": "wiki",
            "kind": "documents",
            "type": "confluence",
            "config": {"base_url": "https://fake.atlassian.net", "email": site.email},
            "secret_refs": {"api_token": "env:KB_CONFLUENCE_TOKEN"},
        },
    )
    assert declared.status_code == 201, declared.text
    monkeypatch.delenv("CHOREGOS_FAKES")
    monkeypatch.setattr(confluence, "TRANSPORT", site.transport())
    source = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "confluence", "name": "Engineering wiki", "connector": "wiki", "config": config},
    )
    assert source.status_code == 201, source.text
    return dict(source.json())


async def test_a_confluence_page_tree_syncs_and_follows_the_source(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.fakes.confluence import FakeConfluence

    site = FakeConfluence()
    site.add_space("ENG")
    site.put_page("ENG", "100", "Handbook", "<p>how we work</p>")
    site.put_page("ENG", "101", "Deploy guide", "<h2>Rollout</h2><p>use argo rollouts</p>", parent="100")
    site.put_page("ENG", "102", "Runbook", "<p>restart the pod</p>", parent="101")
    site.put_page("ENG", "103", "Pager", "<p>call the oncall</p>", parent="100")
    site.put_page("ENG", "900", "Lunch", "<p>pasta</p>")
    source = await _confluence(client, monkeypatch, site, space_key="ENG", page_id="100")
    assert source["config"] == {
        "space_key": "ENG", "page_id": "100", "include_root": True, "include_attachments": False,
    }  # fmt: skip

    first = await _sync(source["id"])
    assert (first["added"], first["unchanged"]) == (4, 0), first
    documents = (await client.get(f"{BASE}/framework")).json()["documents"]
    assert len(documents) == 4
    deploy = next(d for d in documents if "deploy-guide" in d["filename"])
    assert deploy["status"] == "ready"
    assert deploy["source_url"] == "https://fake.atlassian.net/wiki/spaces/ENG/pages/101/Deploy+guide"
    assert "Lunch" not in {d["title"] for d in documents}

    site.requests.clear()
    site.put_page("ENG", "101", "Deploy guide", "<p>use flux and canaries</p>", parent="100")
    second = await _sync(source["id"])
    assert (second["updated"], second["unchanged"], second["deleted"]) == (1, 3, 0), second
    assert [site.fetched(pid) for pid in ("100", "101", "102", "103")] == [0, 1, 0, 0], "only the edited page"
    assert "deploy-guide-101.html" in await _search(client, "canaries")

    site.delete_page("102")
    site.restrict("103")
    third = await _sync(source["id"])
    assert third["deleted"] == 2, third
    assert {d["filename"] for d in (await client.get(f"{BASE}/framework")).json()["documents"]} == {
        "handbook-100.html", "deploy-guide-101.html",
    }  # fmt: skip


async def test_a_listing_that_fails_deletes_nothing(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.fakes.confluence import FakeConfluence
    from choregos_adapters.http import rest

    monkeypatch.setattr(rest, "_backoff", lambda response, attempt: 0.0)
    site = FakeConfluence()
    site.add_space("ENG")
    site.put_page("ENG", "100", "Handbook", "<p>how we work</p>")
    source = await _confluence(client, monkeypatch, site, space_key="ENG")
    assert (await _sync(source["id"]))["added"] == 1
    site.fail_next("/pages", 503, times=10)
    with pytest.raises(Exception, match="503"):
        await _sync(source["id"])
    assert len((await client.get(f"{BASE}/framework")).json()["documents"]) == 1


@pytest.mark.parametrize(
    ("config", "needle"),
    [
        ({}, "space_key"),
        ({"space_key": "eng lowercase"}, "space_key"),
        ({"space_key": "ENG", "page_id": "abc"}, "page_id"),
        ({"space_key": "ENG", "include_attachments": "yes"}, "include_attachments"),
        ({"space_key": "ENG", "labels": ["x"] * 21}, "labels"),
    ],
)
async def test_a_confluence_source_is_refused_before_its_first_sync(
    client: AsyncClient, admin: str, config: dict[str, Any], needle: str
) -> None:
    await client.post(BASE, json={"slug": "framework", "name": "Framework", "serving_mode": "index"})
    declared = await client.post(
        "/api/v1/orgs/varga/connectors",
        json={"name": "wiki", "kind": "documents", "type": "confluence",
              "config": {"base_url": "https://x.atlassian.net", "email": "a@b.c"},
              "secret_refs": {"api_token": "env:KB_CONFLUENCE_TOKEN"}},
    )  # fmt: skip
    assert declared.status_code == 201, declared.text
    refused = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "confluence", "name": "w", "connector": "wiki", "config": config},
    )
    assert refused.status_code == 422 and needle in refused.text, refused.text


async def test_a_confluence_source_refuses_a_connector_of_another_type(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _sharepoint(client, monkeypatch)
    refused = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "confluence", "name": "w", "connector": "sharepoint", "config": {"space_key": "ENG"}},
    )
    assert refused.status_code == 422 and "documents/sharepoint" in refused.text, refused.text
    missing = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "confluence", "name": "w", "config": {"space_key": "ENG"}},
    )
    assert missing.status_code == 422 and "connector" in missing.text


async def test_the_confluence_connector_type_is_listed_with_its_secret_field(
    client: AsyncClient, admin: str
) -> None:
    types = {(t["kind"], t["type"]): t for t in (await client.get("/api/v1/connectors/types")).json()}
    spec = types[("documents", "confluence")]
    assert spec["secret_fields"] == ["api_token"] and spec["capabilities"] == ["documents"]
    assert set(spec["config_schema"]["required"]) == {"base_url", "email"}
    clear = await client.post(
        "/api/v1/orgs/varga/connectors",
        json={"name": "w", "kind": "documents", "type": "confluence",
              "config": {"base_url": "https://x.atlassian.net", "email": "a@b.c", "api_token": "plain"}},
    )  # fmt: skip
    assert clear.status_code == 422
