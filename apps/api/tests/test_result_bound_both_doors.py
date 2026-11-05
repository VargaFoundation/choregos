"""Both doors read the same amount of every answer (#1383, ADR 0122 §8).

#1374 made the run's tool server show the model as much of a result as the API lets through, at the
platform's default bound (64 KiB). Two answers still reached a script whole and the model cut there:

- an operation, a catalogue entry or a plugin tool that raises its own cap (the ontology's 256 KiB
  pages): the tool server could not know the cap, and cut at the default;
- `web_fetch`, which the API did not bound at all: a page reached a script up to its 2 MiB read and
  the model cut at 64 KiB.

The API now says the bound it held a result to (`result_max_bytes` in `callRunTool`'s answer), the
tool server cuts at it, and `web_fetch`'s answer is bounded like any tool result: the platform's
default bound, the page's text kept up to it with the marker, what was fetched kept beside it.
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .kits.extension_tools import run_extension_tool
from .kits.netops import PFSENSE_KEY, READER_TOKEN
from .kits.netops import declare_netops_connectors as _declare
from .kits.netops import netops_run as _run
from .test_network_policy_api import _run as _plain_run
from .test_web_fetch_tool import PAGE, _ready, web  # noqa: F401 - `web` is a fixture

pytestmark = pytest.mark.asyncio

#: A plugin's tool that declares 256 KiB, as the ontology's page of objects does (S40-17c).
PLUGIN = """
from choregos_api.extensions import declare_tool_provider

PAGE_OF_HOSTS = {
    "name": "host_search",
    "description": "A page of hosts.",
    "inputSchema": {"type": "object", "properties": {"count": {"type": "integer"}}},
    "result_max_bytes": 262144,
}


async def list_tools(session, run, project):
    return [PAGE_OF_HOSTS]


async def call_tool(session, run, project, name, arguments):
    hosts = [
        {"id": f"host-{n}", "name": f"web-{n:05d}.example", "note": "x" * 160}
        for n in range(arguments["count"])
    ]
    return 200, {"objects": hosts, "next_cursor": "after-host"}


def plug_in():
    declare_tool_provider("plugin_hosts", list_tools, call_tool)
"""


@pytest.fixture
def plugin(tmp_path: pathlib.Path) -> Iterator[None]:
    from choregos_api.extensions import reset

    root = tmp_path / "site"
    root.mkdir()
    (root / "plugin_hosts.py").write_text(PLUGIN, encoding="utf-8")
    info = root / "plugin_hosts-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: plugin_hosts\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nplugin_hosts = plugin_hosts:plug_in\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("plugin_hosts", None)
        reset()


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _door(answer: dict[str, Any]) -> tuple[str, Any]:
    """What the run's tool server shows the model of the API's answer: its whole text, and the
    result's JSON past the notices, parsed."""
    from choregos_tools_mcp.answer import render_answer

    text = render_answer(answer)["content"][0]["text"]
    [body] = [part for part in text.split("\n\n") if part.startswith(("{", "["))]
    return text, json.loads(body)


# ───────────────────────── a raised cap ─────────────────────────


async def test_the_prompt_loop_lets_through_what_the_ceiling_allows() -> None:
    """One number: the API's default ceiling on a declared cap is what the prompt loop reads of one
    tool answer (`choregos_runner.prompt`), its notices beside it."""
    from choregos_api.config import Settings
    from choregos_core.mcp import RESULT_CEILING_BYTES

    assert Settings.model_fields["broker_result_ceiling_bytes"].default == RESULT_CEILING_BYTES == 1_048_576


async def test_a_plugin_s_raised_cap_reaches_the_model_and_the_script_alike(
    plugin: None, client: AsyncClient, project: dict[str, Any]
) -> None:
    """A 200 KiB page of a tool capped at 256 KiB: whole through both doors, the same data."""
    from choregos_runner.tool_client import unwrapped

    run_id, headers = await run_extension_tool(project["id"])
    called = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/host_search", headers=headers, json={"count": 1_000}
    )
    assert called.status_code == 200, called.text
    answer = called.json()
    result = answer["result"]
    assert answer["result_max_bytes"] == 262_144, "the API says the bound it held the result to"
    assert 200_000 < _size(result) < 262_144 and "truncated" not in result, "200 KB, under its own cap"

    text, read = _door(answer)
    assert "[cut by the tool server" not in text, "the tool server cuts at the tool's cap, not at 64 KiB"
    assert read == result == unwrapped("host_search", result), "the model reads what a script reads"
    assert read["next_cursor"] == "after-host", "the page keeps its cursor"


@pytest.fixture
def e1(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """The netops estate with E1's firewall (S57-09)."""
    import choregos_adapters
    from choregos_adapters.fakes.netops import (
        FakeCiliumKubernetes,
        FakeRepo,
        recorded_flows,
        ssh_exposure_firewall,
    )

    firewall = ssh_exposure_firewall()
    firewall.api_key = PFSENSE_KEY
    fakes = {
        "pfsense": firewall,
        "kubernetes": FakeCiliumKubernetes(token=READER_TOKEN),
        "flows": recorded_flows(),
        "repo": FakeRepo(),
    }
    monkeypatch.setattr(choregos_adapters, "FAKE_NETOPS", fakes)
    monkeypatch.setenv("CONNECTOR_NETOPS_TEST_PFSENSE_KEY", PFSENSE_KEY)
    monkeypatch.setenv("CONNECTOR_NETOPS_TEST_READER_TOKEN", READER_TOKEN)
    yield fakes


async def test_an_operation_whose_cap_an_administrator_raised_reaches_both_doors_whole(
    client: AsyncClient, project: dict[str, Any], e1: dict[str, Any]
) -> None:
    """E1's firewall grown to about 200 KB of rules, its operation's cap raised to 256 KiB."""
    from choregos_runner.tool_client import unwrapped

    await _declare(client)
    firewall = e1["pfsense"]
    rules = list(firewall.rules)
    firewall.rules = [
        {**rule, "tracker": f"{copy}{rule['tracker']}"} for copy in range(1, 5) for rule in rules
    ]
    base, headers = await _run(client, project)
    operation = "/api/v1/orgs/varga/connectors/pfsense/operations/list_firewall_rules"

    default = (await client.post(f"{base}/pfsense__list_firewall_rules", headers=headers, json={})).json()
    assert default["result_max_bytes"] == 65_536 and "truncated" in default["result"], "cut at the default"

    assert (await client.patch(operation, json={"result_max_bytes": 262_144})).status_code == 200
    raised = (await client.post(f"{base}/pfsense__list_firewall_rules", headers=headers, json={})).json()
    result = raised["result"]
    assert raised["result_max_bytes"] == 262_144 and "truncated" not in result
    listed = result["structuredContent"]
    assert len(listed["rules"]) == 4 * 184 and 150_000 < _size(listed) < 262_144

    text, read = _door(raised)
    assert "[cut by the tool server" not in text
    assert (
        read == {"structuredContent": listed} and unwrapped("pfsense__list_firewall_rules", result) == listed
    )


# ───────────────────────── web_fetch ─────────────────────────


async def test_a_page_over_the_bound_reaches_both_doors_cut_at_the_same_place(
    client: AsyncClient,
    project: dict[str, Any],
    web: Any,  # noqa: F811 - the fixture imported above
) -> None:
    """A 1 MiB page: the API bounds it at the platform's default, the page's text kept up to it with the
    marker and what was fetched beside it; the model and a script read the same cut."""
    from choregos_runner.tool_client import unwrapped

    line = "a line of a long page about feature flags\n"
    page = line * (1_048_576 // len(line))
    web.add(PAGE, page, content_type="text/plain")
    base, headers = await _ready(client, project)
    answer = (await client.post(f"{base}/web_fetch", headers=headers, json={"url": PAGE})).json()
    assert answer["status_code"] == 200, answer
    result = answer["result"]
    assert answer["result_max_bytes"] == 65_536 and _size(result) <= 65_536, "bounded like any tool result"
    cut = result["content"][0]["text"]
    shown = result["truncated"]["returned_bytes"]
    assert 60_000 < shown < 65_536 and cut[:shown] == page[:shown], "the page's text, intact up to the bound"
    assert cut.endswith(
        f"[truncated by the platform: {shown} of {result['truncated']['total_bytes']} bytes; ask for less]"
    )
    fetched = result["structuredContent"]
    assert fetched["url"] == PAGE and fetched["bytes"] == len(page), "what was fetched, whole beside the cut"

    script = unwrapped("web_fetch", result)
    text, read = _door(answer)
    assert "[cut by the tool server" not in text, "the tool server does not cut again"
    assert read["content"][0]["text"] == script["text"] == cut, "the model reads what a script reads"
    assert read["structuredContent"] == script["structured"] == fetched


async def test_a_page_under_the_bound_is_whole_and_says_its_bound(
    client: AsyncClient,
    project: dict[str, Any],
    web: Any,  # noqa: F811 - the fixture imported above
) -> None:
    web.add(PAGE, "short page", content_type="text/plain")
    base, headers = await _ready(client, project)
    answer = (await client.post(f"{base}/web_fetch", headers=headers, json={"url": PAGE})).json()
    assert answer["result_max_bytes"] == 65_536
    assert answer["result"]["content"][0]["text"] == "short page" and "truncated" not in answer["result"]


async def test_a_job_step_s_page_is_masked_never_cut(
    client: AsyncClient,
    project: dict[str, Any],
    web: Any,  # noqa: F811 - the fixture imported above
) -> None:
    """A job reads the answer, not a model (S40-13b): its page is masked, whole, and no bound is named."""
    page = "a line of a long page\n" * 5_000  # 110 KB
    web.add(PAGE, page + "password=hunter2-of-the-page\n", content_type="text/plain")
    await _ready(client, project)
    headers = await _plain_run(project, "run-web-job", backend="job", stage_input={"job": {"command": ["x"]}})
    called = await client.post(
        "/api/v1/internal/runs/run-web-job/tools/web_fetch", headers=headers, json={"url": PAGE}
    )
    assert called.status_code == 200, called.text
    answer = called.json()
    assert answer["result_max_bytes"] is None and "truncated" not in answer["result"], "no bound named"
    text = answer["result"]["content"][0]["text"]
    assert text.startswith(page) and "hunter2-of-the-page" not in text, "whole, and masked"
