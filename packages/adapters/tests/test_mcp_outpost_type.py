# SPDX-License-Identifier: Apache-2.0
"""The `mcp-outpost` connector type (S65-13, ADR 0149 §1-3): a tool server that lives at an outpost.

It is declared with an outpost's name and an endpoint alias, has no URL, no secret and no host of
its own, and no path builds a working client without the rendezvous the platform supplies, under
fakes or not. Its client has the two methods the platform calls on a connector's client and is not
an MCP client.
"""

from __future__ import annotations

import pathlib
from typing import Any

import choregos_adapters
import pytest
import yaml
from choregos_adapters import build, egress_hosts, registry, spec_of, validate_config
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.outpost import FakeOutpost
from choregos_adapters.mcp import McpClient
from choregos_adapters.outpost import OutpostToolClient

ROOT = pathlib.Path(__file__).resolve().parents[3]
TOOLS = [{"name": "read_file", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}]
STORED = {"outpost": "factory-floor", "endpoint": "tracker"}


def _outpost() -> FakeOutpost:
    return FakeOutpost(name="factory-floor", endpoints={"tracker": FakeMcpServer(tools=list(TOOLS))})


def test_the_type_is_declared_with_the_contracts_schema_and_no_secret_or_host() -> None:
    spec = spec_of("mcp", "mcp-outpost")
    assert spec is not None and spec.capabilities == ("mcp",)
    assert spec.secret_fields == () and spec.operations == (), "no secret; its tools are discovered"
    contract = yaml.safe_load((ROOT / "packages/contracts/openapi.yaml").read_text(encoding="utf-8"))
    declared = dict(contract["components"]["schemas"]["McpOutpostConnectorConfig"])
    declared.pop("description")
    assert spec.config_schema == declared, "the declared schema is the contract's, to the word"
    assert spec.config_schema["additionalProperties"] is False
    # The platform opens no connection for it: whatever its configuration holds, it has no host.
    assert egress_hosts("mcp", "mcp-outpost", {**STORED, "url": "https://evil.test"}) == []
    assert ("mcp", "mcp-outpost") in registry.BUILT_UNDER_FAKES
    assert choregos_adapters.MCP_OUTPOST_TYPE == "mcp-outpost"


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"outpost": "factory-floor"},
        {"endpoint": "tracker"},
        {**STORED, "url": "https://tracker.internal.example.com/mcp"},
        {**STORED, "token": "t"},
        {**STORED, "auth_header": "X-Key"},
        {**STORED, "server": "tracker"},
        {**STORED, "identity_modes": ["user_exchange"]},
        {**STORED, "route": "outpost"},
        {**STORED, "rendezvous": object()},
        {**STORED, "asserted": {"run": "r"}},
        {**STORED, "write_key": "k"},
        {**STORED, "placing": object()},
        {**STORED, "endpoint": "https://tracker.internal.example.com/mcp"},
        {**STORED, "endpoint": "tracker.tools.svc:8080"},
        {**STORED, "endpoint": "Tracker"},
        {**STORED, "endpoint": "-tracker"},
        {**STORED, "endpoint": "t" * 64},
        {**STORED, "endpoint": 7},
        {**STORED, "outpost": "Factory Floor"},
        {**STORED, "outpost": "../x"},
        {**STORED, "outpost": "f" * 64},
        {**STORED, "timeout_s": 0},
        {**STORED, "timeout_s": 121},
        {**STORED, "timeout_s": "30"},
        {**STORED, "timeout_s": True},
    ],
)
def test_an_address_a_secret_or_a_key_of_the_platform_is_refused(config: dict[str, Any]) -> None:
    with pytest.raises(AdapterError):
        validate_config("mcp", "mcp-outpost", config)


def test_a_stored_configuration_passes_and_an_extra_key_is_named() -> None:
    validate_config("mcp", "mcp-outpost", STORED)
    validate_config("mcp", "mcp-outpost", {**STORED, "timeout_s": 1})
    validate_config("mcp", "mcp-outpost", {"outpost": "f" * 63, "endpoint": "t" * 63, "timeout_s": 120})
    with pytest.raises(AdapterError, match="`token`, `url` has no place"):
        validate_config("mcp", "mcp-outpost", {**STORED, "url": "u", "token": "t"})


@pytest.mark.parametrize("fakes", [None, "1"])
def test_the_adapters_factory_builds_nothing_without_a_rendezvous(
    monkeypatch: pytest.MonkeyPatch, fakes: str | None
) -> None:
    """Under fakes too: the type stays itself (`BUILT_UNDER_FAKES`), and is never the fake `mcp`
    client, which would call the fake server as if no outpost stood in between."""
    from choregos_adapters import builtins

    monkeypatch.setitem(registry._REGISTRY, ("mcp", "mcp-outpost"), builtins._mcp_outpost_client)
    if fakes is None:
        monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    else:
        monkeypatch.setenv("CHOREGOS_FAKES", fakes)
    for config in (STORED, {**STORED, "route": "outpost"}, {**STORED, "rendezvous": _outpost()}):
        with pytest.raises(AdapterError, match="only the platform supplies"):
            build("mcp", "mcp-outpost", dict(config))
    with pytest.raises(AdapterError, match="never an address"):
        build("mcp", "mcp-outpost", {"outpost": "factory-floor", "endpoint": "https://x.test"})


async def test_the_adapters_factory_builds_the_client_over_what_the_platform_injects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_adapters import builtins

    monkeypatch.setitem(registry._REGISTRY, ("mcp", "mcp-outpost"), builtins._mcp_outpost_client)
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    outpost = _outpost()
    injected = {
        **STORED,
        "timeout_s": 7,
        "route": "outpost",
        "rendezvous": outpost,
        "asserted": {"run": "r-1"},
    }
    client = build("mcp", "mcp-outpost", injected)
    assert isinstance(client, OutpostToolClient)
    assert [tool.name for tool in await client.list_tools()] == ["read_file"]
    (placed,) = outpost.placed
    assert (placed.endpoint, placed.timeout_s, dict(placed.asserted)) == ("tracker", 7.0, {"run": "r-1"})


def test_the_client_has_the_two_methods_and_is_no_mcp_client() -> None:
    assert not issubclass(OutpostToolClient, McpClient), "it extends no MCP client (ADR 0149 §3)"
    assert OutpostToolClient.__mro__[1] is object
    for method in ("list_tools", "call_tool"):
        assert callable(getattr(OutpostToolClient, method)) and callable(getattr(McpClient, method))


def test_the_package_imports_no_platform_and_the_root_does_not_import_the_mcp_client() -> None:
    """`packages/*` never import `choregos_api`; and the adapters' root, which the outposts' door
    imports, must not pull in the MCP client through this package (ADR 0148 §12)."""
    import subprocess
    import sys

    probe = (
        "import sys, choregos_adapters, choregos_adapters.sealing;"
        "print('choregos_adapters.mcp' in sys.modules, 'choregos_adapters.outpost' in sys.modules);"
        "import choregos_adapters.outpost, choregos_adapters.fakes.outpost;"
        "print(any(name.split('.')[0] == 'choregos_api' for name in sys.modules))"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.split() == ["False", "False", "False"], done.stdout


def test_the_fake_outpost_of_the_process_serves_every_alias_from_the_current_fake_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(choregos_adapters, "FAKE_OUTPOST", None)
    first, second = FakeMcpServer(tools=list(TOOLS)), FakeMcpServer(tools=[])
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", first)
    fake = choregos_adapters.fake_outpost()
    assert fake is choregos_adapters.fake_outpost() is choregos_adapters.FAKE_OUTPOST
    assert fake._server("anything") is first
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", second)
    assert fake._server("anything") is second, "a test that replaces the fake server is followed"
