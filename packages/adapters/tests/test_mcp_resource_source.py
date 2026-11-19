"""The resources of an MCP server as a knowledge source (ADR 0191), against the scriptable fake."""

from __future__ import annotations

import base64
import json

import pytest
from tranibor_adapters.documents.base import ItemUnreadableError, SourceRefusedError
from tranibor_adapters.documents.mcp import McpResourceSource, checked_config, file_name
from tranibor_adapters.fakes.mcp import FakeMcpServer
from tranibor_adapters.mcp import McpClient, McpError

OKF = "---\ntype: Table\ntitle: Orders\n---\n# Orders\n\nOne row per order.\n"


def _server(**kwargs: object) -> FakeMcpServer:
    resources = [
        {
            "uri": "catalog://sales/orders",
            "name": "orders",
            "mimeType": "text/markdown",
            "annotations": {"lastModified": "2026-11-18T09:00:00Z"},
        },
        {"uri": "catalog://sales/customers", "name": "customers.md", "mimeType": "text/markdown"},
        {"uri": "catalog://hr/people", "name": "people", "mimeType": "text/markdown"},
        {"uri": "https://docs.example/guide.pdf", "name": "guide", "mimeType": "application/pdf", "size": 9},
        {"uri": "catalog://sales/logo", "name": "logo", "mimeType": "image/png"},
    ]
    contents = {
        "catalog://sales/orders": [{"uri": "catalog://sales/orders", "text": OKF}],
        "catalog://sales/customers": [{"uri": "catalog://sales/customers", "text": "# Customers\n"}],
        "https://docs.example/guide.pdf": [
            {"uri": "https://docs.example/guide.pdf", "blob": base64.b64encode(b"%PDF-1.7").decode()}
        ],
    }
    return FakeMcpServer(resources=resources, contents=contents, page_size=2, **kwargs)  # type: ignore[arg-type]


def _source(server: FakeMcpServer, **config: object) -> McpResourceSource:
    client = McpClient("https://catalog.test/mcp", token="key", transport=server.transport())
    return McpResourceSource(client, **checked_config(dict(config)))


@pytest.mark.parametrize("sse", [False, True], ids=["json", "sse"])
async def test_every_page_of_the_listing_becomes_an_item_and_the_listing_is_complete(sse: bool) -> None:
    server = _server(sse=sse, token="key")
    changes = await _source(server).changes(None)
    assert changes.full is True, "a resource no longer listed must leave the base"
    by_ref = {i.ref: i for i in changes.items}
    assert set(by_ref) == {
        "catalog://hr/people",
        "catalog://sales/customers",
        "catalog://sales/logo",
        "catalog://sales/orders",
        "https://docs.example/guide.pdf",
    }
    assert [m for m, _, _ in server.received].count("resources/list") == 3, "five resources, three pages"
    assert by_ref["catalog://sales/orders"].name == "orders.md"
    assert by_ref["catalog://sales/customers"].name == "customers.md", "no second extension"
    assert by_ref["https://docs.example/guide.pdf"].name == "guide.pdf"
    assert by_ref["https://docs.example/guide.pdf"].url == "https://docs.example/guide.pdf"
    assert by_ref["https://docs.example/guide.pdf"].size == 9
    assert by_ref["catalog://sales/orders"].url is None, "a URI no browser opens is not a link"


async def test_a_declared_modification_is_the_version_and_its_absence_reads_again() -> None:
    source = _source(_server())
    first, second = await source.changes(None), await source.changes(None)
    orders = [i.version for c in (first, second) for i in c.items if i.ref == "catalog://sales/orders"]
    assert orders == ["modified:2026-11-18T09:00:00Z"] * 2
    plain = [i.version for c in (first, second) for i in c.items if i.ref == "catalog://sales/customers"]
    assert plain[0] != plain[1], "without lastModified, every sync reads it again (the digest decides)"


async def test_the_prefix_and_the_media_types_narrow_what_is_read() -> None:
    server = _server()
    only_sales = await _source(server, uri_prefix="catalog://sales/").changes(None)
    assert {i.ref for i in only_sales.items} == {
        "catalog://sales/customers",
        "catalog://sales/logo",
        "catalog://sales/orders",
    }
    markdown = await _source(server, uri_prefix="catalog://sales/", mime_types=["text/markdown"]).changes(
        None
    )
    assert {i.ref for i in markdown.items} == {"catalog://sales/customers", "catalog://sales/orders"}


async def test_a_text_is_utf8_and_a_blob_is_base64() -> None:
    source = _source(_server())
    items = {i.ref: i for i in (await source.changes(None)).items}
    assert await source.fetch(items["catalog://sales/orders"]) == OKF.encode()
    assert await source.fetch(items["https://docs.example/guide.pdf"]) == b"%PDF-1.7"


async def test_a_resource_gone_since_the_listing_is_skipped_not_fatal() -> None:
    source = _source(_server())
    items = {i.ref: i for i in (await source.changes(None)).items}
    with pytest.raises(ItemUnreadableError, match="gone since the listing"):
        await source.fetch(items["catalog://hr/people"])


async def test_an_unreadable_content_is_skipped() -> None:
    server = _server()
    server.contents["catalog://hr/people"] = [{"uri": "catalog://hr/people"}]
    server.contents["catalog://sales/logo"] = [{"uri": "catalog://sales/logo", "blob": "%%%"}]
    source = _source(server)
    items = {i.ref: i for i in (await source.changes(None)).items}
    with pytest.raises(ItemUnreadableError, match="neither"):
        await source.fetch(items["catalog://hr/people"])
    with pytest.raises(ItemUnreadableError, match="base64"):
        await source.fetch(items["catalog://sales/logo"])


async def test_a_listing_that_fails_fails_the_sync_rather_than_emptying_the_base() -> None:
    server = _server(token="right")
    client = McpClient("https://catalog.test/mcp", token="wrong", transport=server.transport())
    with pytest.raises(McpError):
        await McpResourceSource(client).changes(None)


async def test_two_resources_of_one_name_get_two_files() -> None:
    server = FakeMcpServer(
        resources=[
            {"uri": "catalog://a/readme", "name": "README", "mimeType": "text/markdown"},
            {"uri": "catalog://b/readme", "name": "readme", "mimeType": "text/markdown"},
            {"uri": "catalog://c/other", "name": "other", "mimeType": "text/markdown"},
        ]
    )
    names = [i.name for i in (await _source(server).changes(None)).items]
    assert len({n.lower() for n in names}) == 3
    assert names[2] == "other.md"
    assert all(n.lower().startswith("readme-") and n.endswith(".md") for n in names[:2])


def test_a_name_is_made_safe_and_keeps_the_extension_its_type_calls_for() -> None:
    assert file_name("", "catalog://x/dir/My%20Table", "text/markdown") == "My Table.md"
    assert file_name("../../etc/passwd", "catalog://x", "text/plain") == "etc-passwd.txt"
    assert file_name("notes.markdown", "catalog://x", "text/markdown") == "notes.markdown"
    assert file_name("schema", "catalog://x", "text/csv") == "schema.txt"
    assert file_name("blob", "catalog://x", "application/octet-stream") == "blob"
    assert len(file_name("a" * 500, "catalog://x", "text/markdown")) == 200


def test_the_configuration_keeps_what_it_knows_and_refuses_the_rest() -> None:
    assert checked_config({"uri_prefix": "catalog://sales/", "other": 1}) == {
        "uri_prefix": "catalog://sales/"
    }
    assert checked_config({"mime_types": ["Text/Markdown", "text/markdown"]}) == {
        "mime_types": ["text/markdown"]
    }
    assert checked_config({}) == {}
    with pytest.raises(SourceRefusedError):
        checked_config({"uri_prefix": "x" * 2001})
    with pytest.raises(SourceRefusedError):
        checked_config({"mime_types": ["not a type"]})


async def test_a_collection_is_one_document_and_binary_parts_are_not_joined() -> None:
    """The protocol's reference server answers five JSON items for one `collection` resource."""
    items = [{"uri": f"catalog://c/item/{n}", "mimeType": "application/json", "text": f'{{"id": {n}}}'}
             for n in (1, 2)]  # fmt: skip
    server = FakeMcpServer(
        resources=[
            {"uri": "catalog://c/specials", "name": "specials", "mimeType": "application/json"},
            {"uri": "catalog://c/notes", "name": "notes", "mimeType": "text/markdown"},
            {"uri": "catalog://c/mixed", "name": "mixed", "mimeType": "text/markdown"},
            {"uri": "catalog://c/broken", "name": "broken", "mimeType": "application/json"},
        ],
        contents={
            "catalog://c/specials": items,
            "catalog://c/notes": [
                {"uri": "catalog://c/n/1", "text": "# One\n"},
                {"uri": "catalog://c/n/2", "text": "# Two"},
            ],
            "catalog://c/mixed": [
                {"uri": "catalog://c/m/1", "text": "# One"},
                {"uri": "catalog://c/m/2", "blob": "AA=="},
            ],
            "catalog://c/broken": [
                {"uri": "catalog://c/b/1", "text": "{"},
                {"uri": "catalog://c/b/2", "text": "{}"},
            ],
        },
    )  # fmt: skip
    source = _source(server)
    by_name = {i.name: i for i in (await source.changes(None)).items}
    assert (
        await source.fetch(by_name["specials.json"]) == b'[\n  {\n    "id": 1\n  },\n  {\n    "id": 2\n  }\n]'
    )
    assert await source.fetch(by_name["notes.md"]) == b"# One\n\n# Two\n"
    with pytest.raises(ItemUnreadableError, match="not all of them text"):
        await source.fetch(by_name["mixed.md"])
    with pytest.raises(ItemUnreadableError, match="not all of them JSON"):
        await source.fetch(by_name["broken.json"])


async def test_a_long_listing_is_followed_to_its_end_and_a_cursor_loop_is_an_error() -> None:
    import httpx

    many = FakeMcpServer(
        resources=[
            {"uri": f"catalog://t/{n:04}", "name": f"t{n}", "mimeType": "text/plain"} for n in range(240)
        ],
        page_size=2,
    )
    listed = await McpClient("https://catalog.test/mcp", transport=many.transport()).list_resources()
    assert len(listed) == 240, "120 pages: a catalogue pages far beyond a tool list's 50"

    def looping(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "id" not in body:
            return httpx.Response(202)
        result: dict[str, object] = {"resources": [{"uri": "catalog://loop"}], "nextCursor": "again"}
        if body["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"resources": {}}, "serverInfo": {}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    client = McpClient("https://catalog.test/mcp", transport=httpx.MockTransport(looping))
    with pytest.raises(McpError, match="already given"):
        await client.list_resources()
