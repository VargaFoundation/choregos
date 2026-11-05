"""Both doors of a run's tools read the same answer (#1373, #1374, ADR 0122 §8).

ADR 0122 §9 compares a cell whose model calls its tools through the MCP door with a cell whose script
calls them through the tool bridge; §8 makes that comparison fair only once both doors say the same
thing. Here each of S57-09's scenes is played through both real doors against the same fake API: the
`mcp` door's `tools/call` on the run's MCP server, and a script run with `python3 -S`, the generated
client `choregos_tools` and the real tool bridge. For every call, what the model reads (the compact JSON
past the notices) holds the same data as what the script got, whole. Before #1374 the MCP door cut E1's
`lan` rules, nine of E2's namespaces and all of E6's bars at 20,000 characters; before #1373 a script's
`web_fetch` got what was fetched, not the page.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from choregos_orchestrator.activities import eval_doors, evals

#: What the tool server showed the model of an answer before #1374.
OLD_MAX_TEXT = 20_000

#: A script that makes the case's calls through the client and prints what each one gave it.
SCRIPT = """\
import json
import choregos_tools

calls = json.loads({calls!r})
print(json.dumps([choregos_tools.call(c["tool"], **c["arguments"]) for c in calls], ensure_ascii=False))
"""


def _calls(case: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"tool": str(call["tool"]), "arguments": dict(call.get("arguments") or {})}
        for call in case.get("evidence") or []
        if "tool" in call
    ]


def _body(answer: dict[str, Any]) -> str:
    """What the model reads of one `tools/call` answer, past the notices: one line of compact JSON."""
    text = answer["result"]["content"][0]["text"]
    [body] = [part for part in text.split("\n\n") if part.startswith("{")]
    return str(body)


def _data(read: dict[str, Any]) -> Any:
    """The data in what the model read: the structure, and a text the structure does not hold."""
    texts = [block["text"] for block in read.get("content", []) if block.get("type") == "text"]
    if not texts:
        return read["structuredContent"]
    return {"text": "\n".join(texts), "structured": read["structuredContent"]}


async def _both_doors(name: str, workdir: Path) -> tuple[list[str], list[Any], dict[str, Any]]:
    case = evals.load_agent_case(name)
    assert case is not None
    calls = _calls(case)
    model = eval_doors.EvalApi(case)
    answers = await eval_doors.through_mcp(model, calls)
    script = eval_doors.EvalApi(case)
    ran = await eval_doors.through_the_bridge(
        script, {"name": "parity.py", "text": SCRIPT.format(calls=json.dumps(calls))}, workdir
    )
    assert ran["exit_code"] == 0, ran["stderr"]
    assert [c["origin"] for c in model.calls] == ["model"] * len(calls)
    assert [c["origin"] for c in script.calls] == ["script"] * len(calls)
    return [_body(answer) for answer in answers], json.loads(ran["stdout"]), ran


@pytest.mark.parametrize(
    ("name", "cut_before"),
    [
        ("netops-ssh-exposure", 1),  # E1: `pfsense__list_firewall_rules(interface=lan)`
        ("netops-dropped-flows-top", 9),  # E2: nine of the twelve namespaces' `cilium__get_flows`
        ("marketdata-volatility", 20),  # E6: all twenty `marketdata__get_bars`
    ],
)
async def test_the_model_reads_what_the_script_gets_on_each_scene(
    name: str, cut_before: int, tmp_path: Path
) -> None:
    bodies, results, _ = await _both_doors(name, tmp_path)

    assert await _cut_before(name) == cut_before, "the answers the MCP door cut before #1374"
    for body, result in zip(bodies, results, strict=True):
        assert "[cut by the tool server" not in body, "whole, through the MCP door"
        read = json.loads(body)
        assert set(read) == {"structuredContent"}, "the data once: no text that serializes it again"
        assert _data(read) == result, "the model reads the data the script gets"


async def _cut_before(name: str) -> int:
    """How many of the case's answers the MCP door cut before #1374: their result as indented JSON,
    the data twice (its text block and its `structuredContent`), past 20,000 characters."""
    case = evals.load_agent_case(name)
    assert case is not None
    api = eval_doors.EvalApi(case)
    cut = 0
    for call in _calls(case):
        result = (await api.call(call["tool"], call["arguments"])).json()["result"]
        assert "truncated" not in result, "every answer within the platform's bound"
        cut += len(json.dumps(result, ensure_ascii=False, indent=2)) > OLD_MAX_TEXT
    return cut


async def test_a_script_reads_the_pages_the_model_reads(tmp_path: Path) -> None:
    """E5: a web search, then six pages, one of them flagged (`warn`)."""
    case = evals.load_agent_case("research-injected-page")
    assert case is not None
    bodies, results, ran = await _both_doors("research-injected-page", tmp_path)

    search, *pages = results
    assert _data(json.loads(bodies[0])) == search and len(search["results"]) == 6
    for body, page, call in zip(bodies[1:], pages, _calls(case)[1:], strict=True):
        assert _data(json.loads(body)) == page, (
            "the page's text and what was fetched, as the model reads them"
        )
        assert page["text"] == case["web"]["pages"][call["arguments"]["url"]]["text"], "the page's text"
        assert page["structured"]["url"] == call["arguments"]["url"]
    assert "flagged as a suspected injection" in ran["stderr"], "the guard's flag reaches the script"


async def test_a_script_prints_the_first_line_of_a_page_it_fetched(tmp_path: Path) -> None:
    """#1373's own test: `platform.web_fetch(...)["text"]` is the page."""
    case = evals.load_agent_case("research-injected-page")
    assert case is not None
    url = "https://toggly.example.com/licence"
    script = {
        "name": "first_line.py",
        "text": "from choregos_tools import platform\n"
        f"page = platform.web_fetch(url={url!r})\n"
        'print(page["text"].splitlines()[0])\n'
        'print(page["structured"]["title"])\n',
    }
    ran = await eval_doors.through_the_bridge(eval_doors.EvalApi(case), script, tmp_path)

    assert ran["exit_code"] == 0, ran["stderr"]
    page = case["web"]["pages"][url]
    assert ran["stdout"].splitlines() == [page["text"].splitlines()[0], page["title"]]
