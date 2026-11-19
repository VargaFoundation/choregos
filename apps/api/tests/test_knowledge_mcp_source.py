"""A knowledge base fed by an MCP server's resources (ADR 0191, S72-05): any server the organisation
declared, read under the connector's own identity, its listing complete, its OKF headers kept."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .egress_support import ORG, rules_in_db

BASE = "/api/v1/orgs/northwind/knowledge"
SERVER = "https://catalog.example.test/mcp"

ORDERS = (
    "---\ntype: Table\ntitle: Sales orders\nverified: [{by: 'human:alice', at: '2026-11-01'}]\n---\n"
    "# Orders\n\nOne row per customer order, net of returns.\n"
)
LEGACY = "---\ntype: Table\nstatus: deprecated\n---\n# Legacy orders\n\nThe zebrafish table.\n"


@pytest.fixture
def catalog(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The MCP server behind the connector, faked: it demands the connector's key."""
    import tranibor_adapters
    from tranibor_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(
        token="catalog-key",
        page_size=2,
        resources=[
            {
                "uri": "catalog://sales/orders",
                "name": "orders",
                "mimeType": "text/markdown",
                "annotations": {"lastModified": "2026-11-18T09:00:00Z"},
            },
            {"uri": "catalog://sales/legacy", "name": "legacy", "mimeType": "text/markdown"},
            {"uri": "catalog://sales/customers", "name": "customers", "mimeType": "text/markdown"},
            {"uri": "catalog://hr/salaries", "name": "salaries", "mimeType": "text/markdown"},
            {"uri": "catalog://sales/logo", "name": "logo", "mimeType": "image/png"},
        ],
        contents={
            "catalog://sales/orders": [{"uri": "catalog://sales/orders", "text": ORDERS}],
            "catalog://sales/legacy": [{"uri": "catalog://sales/legacy", "text": LEGACY}],
            "catalog://sales/customers": [
                {"uri": "catalog://sales/customers", "text": "# Customers\n\nThe kingfisher segment.\n"}
            ],
            "catalog://hr/salaries": [{"uri": "catalog://hr/salaries", "text": "# Salaries\n"}],
            "catalog://sales/logo": [{"uri": "catalog://sales/logo", "blob": "iVBORw0KGgo="}],
        },
    )
    monkeypatch.setattr(tranibor_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_KB_CATALOG_KEY", "catalog-key")
    yield server


async def _connector(client: AsyncClient, name: str = "catalog", **config: Any) -> None:
    declared = await client.post(
        f"{ORG}/connectors",
        json={
            "name": name,
            "kind": "mcp",
            "type": "mcp",
            "config": {"url": SERVER, **config},
            "secret_refs": {"token": "env:CONNECTOR_KB_CATALOG_KEY"},
        },
    )
    assert declared.status_code == 201, declared.text


async def _base(client: AsyncClient) -> None:
    created = await client.post(BASE, json={"slug": "data", "name": "Data", "serving_mode": "index"})
    assert created.status_code == 201, created.text


async def _source(client: AsyncClient, **config: Any) -> dict[str, Any]:
    created = await client.post(
        f"{BASE}/data/sources",
        json={"kind": "mcp", "name": "Data catalogue", "connector": "catalog", "config": config},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _sync(source_id: str) -> dict[str, Any]:
    from tranibor_api.knowledge.sync import sync_source

    return await sync_source("northwind", source_id)


async def _search(client: AsyncClient, query: str) -> set[str]:
    found = await client.post(f"{BASE}/data/search", json={"query": query})
    assert found.status_code == 200, found.text
    # On PostgreSQL the ranking is hybrid and also returns near documents: what counts is which one
    # holds the word.
    return {hit["filename"] for hit in found.json()["hits"] if query in hit["excerpt"]}


def _reads(server: Any) -> list[str]:
    return [body["params"]["uri"] for method, _, body in server.received if method == "resources/read"]


async def test_an_mcp_servers_resources_feed_a_base_and_follow_the_server(
    client: AsyncClient, admin: str, catalog: Any
) -> None:
    await _base(client)
    await _connector(client)
    source = await _source(client, uri_prefix="catalog://sales/")
    assert source["kind"] == "mcp" and source["config"] == {"uri_prefix": "catalog://sales/"}

    first = await _sync(source["id"])
    assert (first["added"], first["skipped"]) == (3, 1), first
    assert first["skipped_reasons"] == ["logo: format not converted"]
    assert "catalog://hr/salaries" not in _reads(catalog), "outside the prefix: never read"
    sent = {headers.get("authorization") for _, headers, _ in catalog.received}
    assert sent == {"Bearer catalog-key"}, "the connector's own key, on every request"

    documents = {d["filename"]: d for d in (await client.get(f"{BASE}/data")).json()["documents"]}
    assert set(documents) == {"orders.md", "legacy.md", "customers.md"}
    assert documents["orders.md"]["status"] == "ready"
    assert documents["orders.md"]["source"] == "mcp"
    assert await _search(client, "kingfisher") == {"customers.md"}
    assert await _search(client, "zebrafish") == set(), "a deprecated resource is neither served nor searched"

    catalog.received.clear()
    second = await _sync(source["id"])
    assert second["unchanged"] == 3, second
    assert "catalog://sales/orders" not in _reads(catalog), "its lastModified did not move: not read again"
    assert "catalog://sales/customers" in _reads(catalog), "no lastModified: read again, its digest decides"

    catalog.resources = [r for r in catalog.resources if r["uri"] != "catalog://sales/customers"]
    catalog.resources[0]["annotations"] = {"lastModified": "2026-11-19T09:00:00Z"}
    catalog.contents["catalog://sales/orders"] = [
        {"uri": "catalog://sales/orders", "text": ORDERS.replace("net of returns", "gross, the pelican way")}
    ]
    third = await _sync(source["id"])
    assert (third["updated"], third["deleted"]) == (1, 1), third
    assert await _search(client, "kingfisher") == set(), "no longer listed: gone from the base"
    assert await _search(client, "pelican") == {"orders.md"}


async def test_the_okf_header_of_a_resource_is_served_and_its_verified_stays_a_claim(
    client: AsyncClient, admin: str, catalog: Any
) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import undefer
    from tranibor_api.db.models import KnowledgeDocument
    from tranibor_api.db.session import session_scope
    from tranibor_api.knowledge import okf

    await _base(client)
    await _connector(client)
    source = await _source(client, uri_prefix="catalog://sales/orders")
    assert (await _sync(source["id"]))["added"] == 1
    async with session_scope() as session:
        document = (
            await session.execute(select(KnowledgeDocument).options(undefer(KnowledgeDocument.markdown)))
        ).scalar_one()
        served, _ = okf.split(okf.served_document(document))
    assert served is not None
    assert (served["type"], served["title"]) == ("Table", "Sales orders"), "the server's own header wins"
    assert "verified" not in served, "a server's review is not the platform's"
    assert served["source_verified"] == [{"by": "human:alice", "at": "2026-11-01"}]


async def test_a_listing_that_fails_fails_the_sync_and_deletes_nothing(
    client: AsyncClient, admin: str, catalog: Any
) -> None:
    await _base(client)
    await _connector(client)
    source = await _source(client)
    assert (await _sync(source["id"]))["added"] == 4
    catalog.token = "rotated-elsewhere"
    with pytest.raises(Exception, match="refused by the server"):
        await _sync(source["id"])
    body = (await client.get(f"{BASE}/data")).json()
    assert len(body["documents"]) == 4, "a server that refuses is not a server that emptied itself"
    assert body["sources"][0]["status"] == "failed"
    assert "refused by the server" in body["sources"][0]["last_error"]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"connector": None}, "names its `mcp` connector"),
        ({"connector": "nowhere"}, "no `mcp` connector `nowhere`"),
        ({"connector": "wiki"}, "is a documents/confluence connector, not a mcp one"),
        ({"connector": "Catalog"}, 'did you mean "catalog"'),
        ({"config": {"uri_prefix": "x" * 2001}}, "`uri_prefix`"),
        ({"config": {"mime_types": ["markdown"]}}, "`mime_types`"),
    ],
)
async def test_an_mcp_source_is_refused_with_its_reason(
    client: AsyncClient,
    admin: str,
    catalog: Any,
    monkeypatch: pytest.MonkeyPatch,
    body: dict[str, Any],
    reason: str,
) -> None:
    await _base(client)
    await _connector(client)
    monkeypatch.setenv("CONNECTOR_KB_CONFLUENCE_TOKEN", "t")
    wiki = await client.post(
        f"{ORG}/connectors",
        json={"name": "wiki", "kind": "documents", "type": "confluence",
              "config": {"base_url": "https://fake.atlassian.net", "email": "kb@northwind.dev"},
              "secret_refs": {"api_token": "env:CONNECTOR_KB_CONFLUENCE_TOKEN"}},
    )  # fmt: skip
    assert wiki.status_code == 201, wiki.text
    refused = await client.post(
        f"{BASE}/data/sources",
        json={"kind": "mcp", "name": "Data catalogue", "connector": "catalog", "config": {}, **body},
    )
    assert refused.status_code == 422, refused.text
    assert reason in refused.json()["detail"]


async def test_a_connector_that_answers_people_only_is_not_synced(
    client: AsyncClient, admin: str, catalog: Any
) -> None:
    await _base(client)
    await _connector(
        client,
        identity_modes=["user_exchange"],
        token_exchange={"audience": "catalog"},
    )
    source = await _source(client)
    with pytest.raises(Exception, match="answers people only"):
        await _sync(source["id"])
    assert not [m for m, _, _ in catalog.received if m == "resources/list"], "nothing was asked of the server"


async def test_adding_the_source_opens_the_connectors_host_for_the_workers_that_sync_it(
    client: AsyncClient, admin: str, catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tranibor_api.config import reset_settings_cache

    await _base(client)
    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()
    declared = await client.post(
        f"{ORG}/connectors",
        json={"name": "catalog", "kind": "mcp", "type": "mcp", "config": {"url": SERVER},
              "secret_refs": {"token": "env:CONNECTOR_KB_CATALOG_KEY"}, "allow_egress": True},
    )  # fmt: skip
    assert declared.status_code == 201, declared.text
    asked = await client.post(
        f"{BASE}/data/sources",
        json={"kind": "mcp", "name": "Data catalogue", "connector": "catalog", "config": {}},
    )
    assert asked.status_code == 409, asked.text
    assert {(v["host"], v["used_by"]) for v in asked.json()["verdicts"] if v["org"] == "absent"} == {
        ("catalog.example.test", "workers")
    }, "the API already reaches it; the workers that sync it do not"
    done = await client.post(
        f"{BASE}/data/sources",
        json={
            "kind": "mcp",
            "name": "Data catalogue",
            "connector": "catalog",
            "config": {},
            "allow_egress": True,
        },
    )
    assert done.status_code == 201, done.text
    (rule,) = [r for r in await rules_in_db("allowed") if r.host == "catalog.example.test"]
    assert set(rule.used_by) == {"api", "workers"}
    assert (await _sync(done.json()["id"]))["added"] == 4, "the workers' scope now lets the sync through"


async def test_a_connector_behind_the_organisations_gateway_is_synced_through_it(
    client: AsyncClient, admin: str, catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`resources/list` and `resources/read` go through the gateway like a run's tool call, signed as
    the source (ADR 0191 §7): the gateway relays them to the server it names."""
    import tranibor_adapters
    from tranibor_adapters.fakes.mcp_gateway import FakeMcpGateway, GatewayUpstream
    from tranibor_api.secret_store import FakeSecretStore, set_secret_store
    from tranibor_api.services import mcp_gateway as service

    set_secret_store(FakeSecretStore())
    service.clear_token_cache()
    try:
        state = await client.put(
            f"{ORG}/mcp-gateway",
            json={"mode": "available", "endpoint": "https://gw.example.test/{server}/mcp", "routing": "path",
                  "auth": {"type": "jwt_assertion"}, "audience": "gw"},
        )  # fmt: skip
        assert state.status_code == 200, state.text
        gateway = FakeMcpGateway(
            auth="jwt_assertion",
            jwks=(await client.get(f"{ORG}/mcp-gateway/jwks.json")).json(),
            audience="gw",
            issuer=state.json()["assertion"]["issuer"],
            upstreams={"catalog": GatewayUpstream(server=catalog, token="catalog-key")},
        )
        monkeypatch.setattr(tranibor_adapters, "FAKE_MCP_GATEWAY", gateway)
        declared = await client.post(
            f"{ORG}/connectors",
            json={"name": "catalog", "kind": "mcp", "type": "mcp-gateway", "config": {"server": "catalog"}},
        )
        assert declared.status_code == 201, declared.text
        await _base(client)
        source = await _source(client, uri_prefix="catalog://sales/")

        summary = await _sync(source["id"])
        assert (summary["added"], summary["skipped"]) == (3, 1), summary
        relayed = [c for c in gateway.calls if c.method.startswith("resources/")]
        assert {c.method for c in relayed} == {"resources/list", "resources/read"}
        assert {c.server for c in relayed} == {"catalog"}
        assert {(c.claims or {}).get("sub") for c in relayed} == {"knowledge-source:Data catalogue"}
        assert "catalog://sales/orders" in _reads(catalog), "the server behind the gateway was read"
    finally:
        set_secret_store(None)
        service.clear_token_cache()


async def test_unversioned_resources_beyond_the_bases_limit_are_never_read(
    client: AsyncClient, admin: str, catalog: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A large unversioned catalogue costs at most the base's limit in reads per sync: what is beyond
    the limit is listed (over many pages) and skipped, never read; what is in the base is read again."""
    from tranibor_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_KNOWLEDGE_MAX_DOCUMENTS", "20")
    reset_settings_cache()
    catalog.resources = [
        {"uri": f"catalog://bulk/{n:03}", "name": f"table-{n:03}", "mimeType": "text/markdown"}
        for n in range(130)
    ]
    catalog.contents = {r["uri"]: [{"uri": r["uri"], "text": f"# {r['name']}\n"}] for r in catalog.resources}
    await _base(client)
    await _connector(client)
    source = await _source(client)

    first = await _sync(source["id"])
    assert (first["added"], first["skipped"]) == (20, 110), first
    assert len([m for m, _, _ in catalog.received if m == "resources/list"]) == 65, "65 pages, all followed"
    assert len(_reads(catalog)) == 20, "beyond the limit: listed, not read"

    catalog.received.clear()
    second = await _sync(source["id"])
    assert (second["unchanged"], second["skipped"]) == (20, 110), second
    assert sorted(_reads(catalog)) == [f"catalog://bulk/{n:03}" for n in range(20)]
