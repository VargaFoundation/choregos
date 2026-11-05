"""A tool result's data is bounded once, and both doors of a run's tools read the same of it (#1373,
#1374, ADR 0122 §8).

The platform bounds what an agent reads of a result (`broker_result_max_bytes`, #755). A connector that
is not MCP answers through `broker._call`, which writes its data twice: serialized in a text block, and
as `structuredContent`. The bound measured both, so a directory's answer was cut at about 32 KiB of
data: E1's whole rule list (44 KB) never reached an agent. It now counts the data once; a text that
says something else (`web_fetch`'s page beside what was fetched) is still counted with it.

The two doors read one answer: the run's tool server shows the model compact JSON of the result, its
data once, up to the same bound (`choregos_tools_mcp.answer`); a script's client gets the same data
(`choregos_runner.tool_client.unwrapped`), the page's text included.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .kits.netops import PFSENSE_KEY, READER_TOKEN
from .kits.netops import declare_netops_connectors as _declare
from .kits.netops import netops_run as _run
from .test_web_fetch_tool import HTML, PAGE, _ready, web  # noqa: F401 - `web` is a fixture

pytestmark = pytest.mark.asyncio


def _directory(raw: Any) -> dict[str, Any]:
    """`broker._call`'s MCP result for a connector that is not MCP."""
    return {
        "content": [{"type": "text", "text": json.dumps(raw, ensure_ascii=False, default=str)}],
        "structuredContent": raw,
    }


def _rules(size: int) -> dict[str, Any]:
    """A firewall's rule list of about `size` bytes of JSON."""
    rule = {"interface": ["lan"], "type": "pass", "protocol": "tcp", "descr": "x" * 60}
    count = size // len(json.dumps({**rule, "id": 100, "tracker": "1000000100"}))
    return {"rules": [{**rule, "id": n, "tracker": str(1_000_000_000 + n)} for n in range(count)]}


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _door_body(answer: dict[str, Any]) -> Any:
    """What the run's tool server shows the model of the API's answer, past its notices, parsed."""
    from choregos_tools_mcp.answer import render_answer

    text = render_answer(answer)["content"][0]["text"]
    [body] = [part for part in text.split("\n\n") if part.startswith("{")]
    return json.loads(body)


# ───────────────────────── the bound, once ─────────────────────────


async def test_the_tool_server_shows_the_model_what_the_api_lets_through() -> None:
    """One number, the platform's: the API's default bound is the tool server's (#1374)."""
    from choregos_api.config import Settings
    from choregos_core.mcp import RESULT_MAX_BYTES
    from choregos_tools_mcp.answer import MAX_NOTICES, MAX_TEXT

    assert Settings.model_fields["broker_result_max_bytes"].default == RESULT_MAX_BYTES == 65_536
    assert MAX_TEXT == RESULT_MAX_BYTES + MAX_NOTICES


async def test_a_directory_s_data_is_counted_once() -> None:
    from choregos_api.services.broker import bounded_result, result_limit

    limit = result_limit()
    under = _rules(44_000)
    assert 40_000 < _size(under) < limit < _size(_directory(under)), "44 KB of data, 90 KB as a result"
    kept = bounded_result(_directory(under))
    assert "truncated" not in kept and kept["structuredContent"] == under, "whole, its structure kept"

    over = _rules(70_000)
    assert _size(over) > limit
    cut = bounded_result(_directory(over))
    assert "structuredContent" not in cut, "half an object is not the object"
    assert _size(cut) <= limit and cut["content"][0]["text"].endswith("bytes; ask for less]")
    # The size the bound measured: the data once, not the 150 KB of the data twice.
    assert _size(over) < cut["truncated"]["total_bytes"] < _size(over) + 100


async def test_a_text_that_says_something_else_is_counted_with_the_structure() -> None:
    """A text block that is not the structure serialized is other data: both count."""
    from choregos_api.services.broker import bounded_result, result_limit

    page = "line of a page\n" * 2_500  # 37 KB
    fetched = {"url": "https://docs.example.com/guide", "notes": "y" * 37_000}
    answer = {"content": [{"type": "text", "text": page}], "structuredContent": fetched}
    assert _size(fetched) < result_limit() < _size(answer)
    cut = bounded_result(answer)
    assert cut["truncated"]["total_bytes"] > result_limit(), "74 KB of two things: cut"
    # What is cut is the text; the structure is another datum, whole beside it when both fit (#1383).
    assert cut["structuredContent"] == fetched and _size(cut) <= result_limit()
    assert page.startswith(cut["content"][0]["text"].rsplit("\n[truncated by the platform", 1)[0])

    larger = {**answer, "structuredContent": {**fetched, "notes": "y" * 70_000}}
    assert "structuredContent" not in bounded_result(larger), "a structure that cannot fit is dropped"

    # A structure a text block serializes is cut with that text: never kept beside it, even if it fits.
    small = {"url": "https://docs.example.com/guide", "notes": "y" * 20_000}
    summary = "line of a summary\n" * 2_800  # 50 KB
    serialized = {
        "content": [{"type": "text", "text": json.dumps(small)}, {"type": "text", "text": summary}],
        "structuredContent": small,
    }
    assert result_limit() < _size(small) + len(summary)
    assert "structuredContent" not in bounded_result(serialized), "half an object is not the object"


# ───────────────────────── through the API, and through both doors ─────────────────────────


@pytest.fixture
def e1(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """The netops estate with E1's firewall (S57-09): 184 rules, 44 KB of data."""
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


async def test_e1_s_whole_rule_list_reaches_both_doors_whole(
    client: AsyncClient, project: dict[str, Any], e1: dict[str, Any]
) -> None:
    from choregos_runner.tool_client import unwrapped

    await _declare(client)
    base, headers = await _run(client, project)
    called = await client.post(f"{base}/pfsense__list_firewall_rules", headers=headers, json={})
    assert called.status_code == 200, called.text
    answer = called.json()
    result = answer["result"]
    assert answer["status_code"] == 200 and "truncated" not in result, "44 KB of data: under the bound"
    rules = result["structuredContent"]
    assert rules["count"] == 184 and len(rules["rules"]) == 184

    assert _door_body(answer) == {"structuredContent": rules}, "the model reads the data, whole, once"
    assert unwrapped("pfsense__list_firewall_rules", result) == rules, "a script reads the same"

    # Past the bound, it is still cut, whichever door reads it.
    firewall = e1["pfsense"]
    firewall.rules += [{**rule, "tracker": f"9{rule['tracker']}"} for rule in firewall.rules[:120]]
    grown = (await client.post(f"{base}/pfsense__list_firewall_rules", headers=headers, json={})).json()
    assert grown["status_code"] == 200 and grown["result"]["truncated"]["total_bytes"] > 65_536
    assert "structuredContent" not in grown["result"]
    total = grown["result"]["truncated"]["total_bytes"]
    assert 65_536 < total < 80_000, "304 rules, 72 KB of data once; twice, they would be 160 KB"


async def test_a_fetched_page_reaches_a_script_as_it_reaches_the_model(
    client: AsyncClient,
    project: dict[str, Any],
    web: Any,  # noqa: F811 - the fixture imported above
) -> None:
    """#1373: the API's own `web_fetch` answer, read by both doors."""
    from choregos_runner.tool_client import unwrapped

    web.add(PAGE, HTML)
    base, headers = await _ready(client, project)
    answer = (await client.post(f"{base}/web_fetch", headers=headers, json={"url": PAGE})).json()
    assert answer["status_code"] == 200, answer

    page = unwrapped("web_fetch", answer["result"])
    assert page["text"].splitlines()[0] == "Guide", "a script reads the page's text"
    assert "Hello world" in page["text"] and page["structured"]["title"] == "The guide"

    read = _door_body(answer)
    assert read["content"][0]["text"] == page["text"], "the model reads the same text"
    assert read["structuredContent"] == page["structured"], "and the same metadata"
