"""The MCP client has English names, every former name still resolves to it, and nothing else moved (S46-01).

The digest of a tool's input schema is persisted in `connector_operations.schema_digest`, so its
canonical form is pinned here to the byte: a rename must not change a stored digest.
"""

from __future__ import annotations

import choregos_adapters
import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.mcp import (
    ClientMcp,
    ErreurMcp,
    McpClient,
    McpError,
    McpTool,
    OutilMcp,
    empreinte_du_schema,
    schema_digest,
)

#: `schema_digest({"type": "object"})` as the client computed it before the rename, under its old name.
OBJECT_SCHEMA_DIGEST = "sha256:a2c799262a3ce3c19ef5cdd983bf3d12b43ab3c426227091b909dcb7054738c0"
#: The same for `{"title": "é"}`: `ensure_ascii=False` is part of the canonical form.
ACCENTED_SCHEMA_DIGEST = "sha256:17fc9dbaed22ce2d07e9a2bb624e57b3cd39cd72e937e0dd0eb4ddf8f03190d6"


def test_the_english_names_are_the_client() -> None:
    assert ClientMcp is McpClient
    assert ErreurMcp is McpError
    assert OutilMcp is McpTool
    assert empreinte_du_schema is schema_digest
    assert McpClient.__name__ == "McpClient"
    assert McpError.__name__ == "McpError"
    assert McpTool.__name__ == "McpTool"
    assert schema_digest.__name__ == "schema_digest"


def test_schema_digest_is_unchanged() -> None:
    assert schema_digest({"type": "object"}) == OBJECT_SCHEMA_DIGEST
    assert empreinte_du_schema({"type": "object"}) == OBJECT_SCHEMA_DIGEST
    # The key order never counts, and a non-ASCII character is hashed as it is, not escaped.
    assert schema_digest({"a": 1, "b": 2}) == schema_digest({"b": 2, "a": 1})
    assert schema_digest({"title": "é"}) == ACCENTED_SCHEMA_DIGEST


def test_the_runner_import_path_still_resolves() -> None:
    from choregos_adapters.mcp.client import ClientMcp as RunnerClientMcp
    from choregos_adapters.mcp.client import McpClient as ClientOfTheModule

    assert RunnerClientMcp is ClientOfTheModule
    client = ClientOfTheModule("https://x.test/mcp")
    assert client.serveur == {} == client.server
    # The former attribute reads the new one, so a server answer shows under both names.
    client.server = {"serverInfo": {"name": "x"}}
    assert client.serveur is client.server


def test_the_fake_mcp_hook_is_named_fake_mcp() -> None:
    # The former name is spelled in two parts, so that a grep for it finds no site in the tree.
    former = "FAUX_" + "MCP"
    assert hasattr(choregos_adapters, "FAKE_MCP")
    assert not hasattr(choregos_adapters, former)


async def test_the_client_says_why_in_english() -> None:
    server = FakeMcpServer(jeton="right")
    client = McpClient("https://x.test/mcp", token="wrong", transport=server.transport())
    with pytest.raises(McpError) as refused:
        await client.list_tools()
    assert "refused by the server (401)" in str(refused.value)
    assert "refusé" not in str(refused.value)  # lang: fr-ok
    assert refused.value.code == 401
