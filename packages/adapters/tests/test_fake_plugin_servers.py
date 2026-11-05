"""A plugin's server on its fake, as an eval matrix connector (#1372).

The scripted matrix reaches a plugin's server as the broker does, over MCP with a bearer token, on a fake
registered for it, and keeps only the tools the server's declaration declares, governed by it. The live
matrix's binding (`fake_scene` on the server's connector) is honoured only where the deployment serves
eval fakes, and only for a scene the server's fake declares.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes.plugin_servers import (
    EVAL_TOKEN,
    FakePluginServer,
    bound_plugin_fake,
    plugin_kind,
    plugin_scene,
    use_fake_plugin_server,
)
from choregos_adapters.fakes.scenes import EVAL_FAKE_SCENES_ENV
from choregos_adapters.mcp import McpError
from choregos_adapters.plugin_servers import PluginServerClient

ROOT = Path(__file__).resolve().parents[3]
RECORDING = ROOT / "tests" / "fixtures" / "plugin_fakes" / "servicenow.json"
DECLARATION = yaml.safe_load(
    (ROOT / "tests" / "fixtures" / "plugins" / "servicenow" / "servers" / "servicenow.yaml").read_text()
)
KIND = "plugin:servicenow/servicenow"


@pytest.fixture
def recorded() -> Iterator[list[FakePluginServer]]:
    """The servicenow server's recorded fake, registered; the fakes it built, for the test to read."""
    built: list[FakePluginServer] = []

    def factory(scene: str) -> Any:
        assert scene == "default"
        built.append(FakePluginServer.from_recording(RECORDING))
        return built[-1].transport()

    use_fake_plugin_server("servicenow", "servicenow", factory)
    yield built
    use_fake_plugin_server("servicenow", "servicenow", None)


def test_a_plugin_kind_names_a_plugin_and_its_server() -> None:
    assert plugin_kind(KIND) == ("servicenow", "servicenow")
    assert [plugin_kind(k) for k in ("firewall", "plugin:servicenow", "plugin:a/b/c")] == [None, None, None]


async def test_without_a_registered_fake_a_plugin_server_cannot_be_played() -> None:
    with pytest.raises(AdapterError, match="no fake of the plugin server servicenow/servicenow"):
        await plugin_scene(KIND, "default", DECLARATION)
    with pytest.raises(AdapterError, match="no plugin read by this process ships"):
        await plugin_scene(KIND, "default", None)


async def test_the_scene_offers_the_declared_operations_with_their_floor_and_the_listed_schemas(
    recorded: list[FakePluginServer],
) -> None:
    scene = await plugin_scene(KIND, "default", DECLARATION)
    operations = {op.name: op for op in scene.operations}
    assert set(operations) == {op["name"] for op in DECLARATION["operations"]}
    assert (operations["get_record"].access, operations["add_work_note"].access) == ("read", "write")
    assert operations["add_comment"].human_approval and operations["resolve_incident"].human_approval
    assert not operations["add_work_note"].human_approval
    assert operations["add_work_note"].platform_arguments == ("action_key",)
    schema = operations["similar_incidents"].input_schema or {}
    assert {"sys_id", "limit"} <= set(schema["properties"]), "the schema the server lists"
    assert isinstance(scene.connector, PluginServerClient), "reached as the broker reaches a server"
    assert scene.fake is recorded[0]


async def test_a_tool_the_server_serves_without_declaring_it_is_not_offered(
    recorded: list[FakePluginServer],
) -> None:
    narrowed = {
        **DECLARATION,
        "operations": [op for op in DECLARATION["operations"] if op["access"] == "read"],
    }
    scene = await plugin_scene(KIND, "default", narrowed)
    assert "add_comment" not in {op.name for op in scene.operations}
    assert any(tool["name"] == "add_comment" for tool in recorded[0].tools), "though the server lists it"


async def test_the_recorded_fake_answers_its_recording_only_and_needs_the_token() -> None:
    fake = FakePluginServer.from_recording(RECORDING)
    client = PluginServerClient("http://servicenow.test/mcp", token=EVAL_TOKEN, transport=fake.transport())
    found = await client.call_tool("get_record", {"table": "incident", "number": "INC0012345"})
    assert found["structuredContent"]["fields"]["number"] == "INC0012345"
    unknown = await client.call_tool("get_record", {"table": "incident", "number": "INC9999999"})
    assert unknown["isError"] is True and "not recorded" in unknown["content"][0]["text"]
    # A platform argument differs per action: it is not part of the match, it is recorded as sent.
    note = {
        "table": "incident",
        "sys_id": "d3010b741883783c781f7241153776f7",
        "text": "x",
        "action_key": "k1",
    }
    await client.call_tool("add_work_note", note)
    assert fake.called("add_work_note") == [note]
    stranger = PluginServerClient("http://servicenow.test/mcp", token="other", transport=fake.transport())
    with pytest.raises(McpError, match="refused by the server"):
        await stranger.list_tools()


def test_a_binding_is_honoured_where_the_deployment_serves_eval_fakes_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound = {"fake_scene": "default"}
    monkeypatch.delenv(EVAL_FAKE_SCENES_ENV, raising=False)
    assert bound_plugin_fake(DECLARATION, bound) is None, "ignored without the switch"
    monkeypatch.setenv(EVAL_FAKE_SCENES_ENV, "1")
    assert bound_plugin_fake(DECLARATION, bound) == "default"
    assert bound_plugin_fake(DECLARATION, {"fake_scene": "other"}) is None, "a scene the fake does not serve"
    assert bound_plugin_fake(DECLARATION, {}) is None
    without = {k: v for k, v in DECLARATION.items() if k != "fake"}
    assert bound_plugin_fake(without, bound) is None, "a server that declares no fake"
