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


async def _confluence(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, site: Any, **config: Any
) -> dict[str, Any]:
    """An `index` base, a `documents/confluence` connector, and a source on the fake site, with the
    real adapter (fakes off) talking to `FakeConfluence` through the module's transport seam."""
    from choregos_adapters.documents import confluence

    monkeypatch.setenv("CONNECTOR_KB_CONFLUENCE_TOKEN", site.token)
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
            "secret_refs": {"api_token": "env:CONNECTOR_KB_CONFLUENCE_TOKEN"},
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


async def test_chosen_pages_without_their_sub_pages_sync_and_one_leaving_is_deleted(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S42-09d: `page_ids` + `descendants: false` mirror exactly those pages."""
    from choregos_adapters.fakes.confluence import FakeConfluence
    from choregos_adapters.http import rest

    monkeypatch.setattr(rest, "_backoff", lambda response, attempt: 0.0)
    site = FakeConfluence()
    site.add_space("ENG")
    site.put_page("ENG", "100", "Handbook", "<p>how we work</p>")
    site.put_page("ENG", "101", "Deploy guide", "<p>use argo rollouts</p>", parent="100")
    site.put_page("ENG", "103", "Pager", "<p>call the oncall</p>")
    source = await _confluence(
        client, monkeypatch, site, space_key="ENG", page_ids=["100", "103", "103"], descendants=False
    )
    assert source["config"] == {
        "space_key": "ENG", "page_ids": ["100", "103"], "descendants": False,
        "include_root": True, "include_attachments": False,
    }  # fmt: skip
    first = await _sync(source["id"])
    assert first["added"] == 2, first
    names = {d["filename"] for d in (await client.get(f"{BASE}/framework")).json()["documents"]}
    assert names == {"handbook-100.html", "pager-103.html"}, "the sub-page 101 was not asked for"
    site.restrict("103")
    with pytest.raises(
        Exception, match="103"
    ):  # a chosen page the account cannot read is refused, not skipped
        await _sync(source["id"])
    assert len((await client.get(f"{BASE}/framework")).json()["documents"]) == 2


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
        ({"space_key": "ENG", "page_ids": ["1"] * 51}, "page_ids"),
        ({"space_key": "ENG", "page_ids": ["12", "ab"]}, "page_ids"),
        ({"space_key": "ENG", "page_ids": ["12"], "page_id": "13"}, "not both"),
        ({"space_key": "ENG", "page_id": "12", "descendants": "no"}, "descendants"),
        ({"space_key": "ENG", "page_id": "12", "descendants": False, "include_root": False}, "no page"),
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
              "secret_refs": {"api_token": "env:CONNECTOR_KB_CONFLUENCE_TOKEN"}},
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


async def test_a_crawl_that_suddenly_lists_nothing_deletes_nothing(client: AsyncClient, admin: str) -> None:
    """The start redirects off the site: no page, and no mass deletion of what was indexed."""
    import httpx
    from choregos_adapters.documents.web import WebCrawlSource

    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    created = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "web", "name": "Site", "config": {"url": "https://docs.example.com/"}},
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]
    state = {"up": True}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="")
        if not state["up"]:
            return httpx.Response(301, headers={"location": "https://other.example.com/"})
        page = {"https://docs.example.com/": '<a href="a">a</a><a href="b">b</a>'}.get(url, f"<h1>{url}</h1>")
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    async def public(host: str) -> list[str]:
        return ["93.184.216.34"]

    def crawl() -> WebCrawlSource:
        return WebCrawlSource(
            url="https://docs.example.com/", transport=httpx.MockTransport(handler), resolver=public
        )

    from choregos_api.knowledge.sync import sync_source

    assert (await sync_source("varga", source_id, adapter=crawl()))["added"] == 3
    state["up"] = False
    second = await sync_source("varga", source_id, adapter=crawl())
    assert second["deleted"] == 0, second
    assert len((await client.get(f"{BASE}/framework")).json()["documents"]) == 3


async def test_a_web_source_starting_on_a_redirect_is_refused(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import respx
    from choregos_adapters.documents import web

    monkeypatch.setenv("CHOREGOS_FAKES", "0")

    async def public(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(web, "resolve", public)
    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    with respx.mock(assert_all_called=False) as mock:
        mock.get("https://www.example.test/").respond(301, headers={"location": "https://example.test/"})
        refused = await client.post(
            f"{BASE}/framework/sources",
            json={"kind": "web", "name": "w", "config": {"url": "https://www.example.test/"}},
        )
    assert refused.status_code == 422, refused.text
    assert "redirects to `https://example.test/`" in refused.json()["detail"]


async def test_a_web_source_keeps_its_exclusions_and_refuses_a_bad_one(
    client: AsyncClient, admin: str
) -> None:
    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    url = {"url": "https://docs.example.com/"}
    ok = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "web", "name": "w", "config": {**url, "exclude": ["/es", "/es/*"]}},
    )
    assert ok.status_code == 201 and ok.json()["config"]["exclude"] == ["/es", "/es/*"]
    bad = await client.post(
        f"{BASE}/framework/sources", json={"kind": "web", "name": "x", "config": {**url, "exclude": ["es"]}}
    )
    assert bad.status_code == 422 and "exclude" in bad.json()["detail"]


async def _git_connector(client: AsyncClient, name: str = "diametral-github") -> None:
    created = await client.post(
        "/api/v1/orgs/varga/connectors",
        json={"name": name, "kind": "documents", "type": "git", "config": {"host": "github.com"},
              "secret_refs": {"token": "env:CONNECTOR_KB_GIT_TOKEN"}},
    )  # fmt: skip
    assert created.status_code == 201, created.text


async def test_a_git_source_names_a_git_connector_for_a_private_repository(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.fakes.documents import fake_source

    monkeypatch.setenv("CONNECTOR_KB_GIT_TOKEN", "t" + "ok-" + "x")

    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    await _git_connector(client)
    created = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "git", "name": "brand", "connector": "diametral-github",
              "config": {"url": "https://github.com/acme/private.git"}},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    assert created.json()["connector"] == "diametral-github"
    fake_source(created.json()["id"]).put("brand.md", "brand.md", b"# Brand\n")
    assert (await _sync(created.json()["id"]))["added"] == 1, "through the connector, here its fake"


async def test_a_git_source_refuses_another_kind_of_connector_and_still_works_without_one(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _sharepoint(client, monkeypatch)
    refused = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "git", "name": "g", "connector": "sharepoint",
              "config": {"url": "https://github.com/acme/private.git"}},
    )  # fmt: skip
    assert refused.status_code == 422 and "a git source needs git" in refused.json()["detail"]
    missing = await client.post(
        f"{BASE}/framework/sources",
        json={
            "kind": "git",
            "name": "g",
            "connector": "nope",
            "config": {"url": "https://github.com/a/b.git"},
        },
    )
    assert missing.status_code == 422 and "no `documents` connector" in missing.json()["detail"]
    public = await client.post(
        f"{BASE}/framework/sources",
        json={"kind": "git", "name": "pub", "config": {"url": "https://github.com/a/public.git"}},
    )
    assert public.status_code == 201 and public.json()["connector"] is None


async def test_a_web_source_with_linked_documents_stores_the_pdf_converted(
    client: AsyncClient, admin: str
) -> None:
    """S42-09b: a PDF the crawl lists goes through the same pipeline as an upload (fakes: no network)."""
    from choregos_adapters.fakes.documents import fake_source
    from choregos_api.db.models import KnowledgeDocument
    from choregos_api.db.session import session_scope

    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    created = await client.post(
        f"{BASE}/framework/sources",
        json={
            "kind": "web",
            "name": "Offer",
            "config": {"url": "https://docs.example.com/offer", "max_depth": 1, "include_documents": True},
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["config"]["include_documents"] is True
    fake_source(created.json()["id"]).put("deck", "deck.pdf", fx.pdf(["Group revenue grew twelve percent"]))
    assert (await _sync(created.json()["id"]))["added"] == 1
    document = (await client.get(f"{BASE}/framework")).json()["documents"][0]
    assert document["filename"] == "deck.pdf" and document["status"] == "ready"
    async with session_scope() as session:
        row = await session.get(KnowledgeDocument, document["id"])
        assert row is not None and row.mime == "application/pdf"


async def test_a_web_source_config_accepts_one_page_and_refuses_a_bad_flag(
    client: AsyncClient, admin: str
) -> None:
    await client.post(BASE, json={"slug": "framework", "name": "Framework"})
    one = await client.post(
        f"{BASE}/framework/sources",
        json={
            "kind": "web",
            "name": "One",
            "config": {"url": "https://docs.example.com/a", "max_depth": 0, "max_pages": 1},
        },
    )
    assert one.status_code == 201, one.text
    assert one.json()["config"] == {"url": "https://docs.example.com/a", "max_depth": 0, "max_pages": 1}
    bad = await client.post(
        f"{BASE}/framework/sources",
        json={
            "kind": "web",
            "name": "x",
            "config": {"url": "https://docs.example.com/", "include_documents": "yes"},
        },
    )
    assert bad.status_code == 422 and "include_documents" in bad.json()["detail"]
