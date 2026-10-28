"""`tracker/plugin` (S43-07): `TrackerAdapter` mapped onto the tracker profile of a plugin server."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.errors import ConfigurationError, UpstreamError
from choregos_adapters.fakes.plugin_tracker import FakePluginTrackerServer
from choregos_adapters.plugin_servers import PluginServerClient
from choregos_adapters.tracker.plugin import PluginTracker
from choregos_contracts import ProjectConfig
from choregos_core.domain import NewItem, TrackerStateMapping


def _tracker(fake: FakePluginTrackerServer, audit: Any = None) -> PluginTracker:
    client = PluginServerClient("http://desk.plugins.svc:8080/mcp", token="t0k", transport=fake.transport())
    return PluginTracker(client, "desk", "desk", audit=audit)


async def test_the_profile_round_trip_is_idempotent_and_audited() -> None:
    fake = FakePluginTrackerServer(token="t0k")
    written: list[tuple[str, dict[str, Any]]] = []

    async def audit(tool: str, details: dict[str, Any]) -> None:
        written.append((tool, details))

    tracker = _tracker(fake, audit)
    key = await tracker.create_item(NewItem(title="Disk full", body="db-prod-01"))
    assert await tracker.create_item(NewItem(title="Disk full", body="db-prod-01")) == key
    assert len(fake.items) == 1, "a replayed create answers the same key"
    await tracker.set_state(key, TrackerStateMapping(status="In Progress", label="choregos:build"))
    await tracker.upsert_status_comment(key, "**Build**", "<!-- choregos:status -->")
    await tracker.upsert_status_comment(key, "**Build**", "<!-- choregos:status -->")
    item = await tracker.fetch_item(key)
    assert (item.state, len(item.comments)) == ("In Progress", 1)
    assert item.comments[0].marker == "[choregos-status:choregos:status]"
    assert await tracker.list_candidates(ProjectConfig.model_validate({"slug": "p", "org": "o"})) == [key]
    await tracker.close_item(key)
    assert await tracker.list_candidates(ProjectConfig.model_validate({"slug": "p", "org": "o"})) == []
    assert [t for t, _ in written].count("tracker_create_item") == 2
    assert {"tracker_set_state", "tracker_upsert_status", "tracker_close"} <= {t for t, _ in written}
    assert (await tracker.test())["ok"] is True


async def test_an_answer_outside_the_profile_is_refused() -> None:
    fake = FakePluginTrackerServer()
    fake.answers["tracker_fetch_item"] = {"structuredContent": {"key": "INC1"}}
    with pytest.raises(UpstreamError, match="outside the tracker profile"):
        await _tracker(fake).fetch_item("INC1")


async def test_another_item_than_the_one_asked_is_refused() -> None:
    fake = FakePluginTrackerServer()
    fake.add("INC2", "Other")
    fake.answers["tracker_fetch_item"] = fake._call("tracker_fetch_item", {"key": "INC2"})
    with pytest.raises(UpstreamError, match="answered `INC2` for `INC1`"):
        await _tracker(fake).fetch_item("INC1")


async def test_a_refusal_is_an_upstream_error() -> None:
    with pytest.raises(UpstreamError, match="refused"):
        await _tracker(FakePluginTrackerServer()).fetch_item("INC404")


async def test_a_server_without_its_token_refuses_and_says_so() -> None:
    fake = FakePluginTrackerServer(token="other")
    with pytest.raises(UpstreamError):
        await _tracker(fake).fetch_item("INC1")


async def test_a_server_missing_a_profile_tool_fails_its_test() -> None:
    fake = FakePluginTrackerServer()
    original = fake._handle

    def without_close(request: Any) -> Any:
        response = original(request)
        if b'"tools/list"' in request.content:
            body = response.json()
            body["result"]["tools"] = [t for t in body["result"]["tools"] if t["name"] != "tracker_close"]
            return type(response)(200, json=body)
        return response

    fake._handle = without_close  # type: ignore[method-assign]
    assert (await _tracker(fake).test()) == {
        "ok": False,
        "server": "plugin:desk/desk",
        "missing": ["tracker_close"],
    }


def test_the_registered_type_takes_only_plugin_and_server() -> None:
    from choregos_adapters import validate_config
    from choregos_adapters.registry import _REGISTRY

    validate_config("tracker", "plugin", {"plugin": "desk", "server": "desk"})
    with pytest.raises(ConfigurationError):
        validate_config("tracker", "plugin", {"plugin": "desk", "server": "desk", "token": "x"})
    with pytest.raises(ConfigurationError, match="built by the platform"):
        _REGISTRY[("tracker", "plugin")]({"plugin": "desk", "server": "desk"})
