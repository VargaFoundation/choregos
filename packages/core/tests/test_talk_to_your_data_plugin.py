# SPDX-License-Identifier: Apache-2.0
"""The "talk to your data" reference plugin (examples/plugins/talk-to-your-data): it loads, its only
need is data_platform under the person's own identity and read-only, and its eval suite passes on a scripted
good run and fails on a run that queries before searching or answers without citing the table."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from choregos_core.conversation_evals import evaluate_case, loop_tools, play_case
from choregos_core.plugins import load_directory

PLUGIN = Path(__file__).resolve().parents[3] / "examples" / "plugins" / "talk-to-your-data"
SEARCH, DESCRIBE, QUERY = (
    "data_platform__search_catalog",
    "data_platform__describe_table",
    "data_platform__query_sql",
)
TOOLS = loop_tools([SEARCH, DESCRIBE, QUERY, "data_platform__preview_table", "data_platform__list_catalog"])
CITED = "You received 1284 orders (source: sales.public.orders) last month."


def _model(*steps: dict[str, Any]) -> Callable[[dict[str, Any]], Any]:
    script = list(steps)

    async def complete(_body: dict[str, Any]) -> dict[str, Any]:
        return {"choices": [{"message": script.pop(0)}]}

    return complete


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"c-{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def _say(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def _cases() -> list[dict[str, Any]]:
    suite = yaml.safe_load((PLUGIN / "evals" / "talk-to-your-data.yaml").read_text())
    cases: list[dict[str, Any]] = suite["cases"]
    return cases


def test_the_plugin_loads_and_asks_the_data_platform_for_the_person_s_identity_read_only() -> None:
    loaded = load_directory(PLUGIN)
    assistant = loaded.assistants["talk-to-your-data"]
    assert assistant["agent"] == "data-analyst"
    (need,) = assistant["tools"]
    assert (
        need["need"] == "data_platform" and need["identity"] == "user_exchange" and need["access"] == "read"
    )
    assert "write" not in {t.get("access") for t in assistant["tools"]}
    assert "query_sql" in need["operations"]


async def test_a_run_that_searches_describes_queries_and_cites_passes() -> None:
    case = _cases()[0]
    complete = _model(
        _call(SEARCH, query="orders"),
        _call(DESCRIBE, table="sales.public.orders"),
        _call(QUERY, sql="SELECT count(*) FROM sales.public.orders"),
        _say(CITED),
    )
    journal = await play_case(case, instructions="x", tools=TOOLS, complete=complete)
    assert journal.names() == [SEARCH, DESCRIBE, QUERY]
    assert evaluate_case(case, journal).passed


async def test_a_run_that_queries_first_and_does_not_cite_fails() -> None:
    case = _cases()[0]
    complete = _model(_call(QUERY, sql="SELECT 1"), _say("About 1284 orders."))
    journal = await play_case(case, instructions="x", tools=TOOLS, complete=complete)
    kinds = " | ".join(c.kind for c in evaluate_case(case, journal).failures)
    assert "call_order" in kinds and "must_cite" in kinds


async def test_an_empty_catalogue_is_not_answered_with_a_query() -> None:
    case = _cases()[1]
    good = await play_case(
        case,
        instructions="x",
        tools=TOOLS,
        complete=_model(_call(SEARCH, query="mars"), _say("No table matches.")),
    )
    assert evaluate_case(case, good).passed
    bad = await play_case(
        case, instructions="x", tools=TOOLS, complete=_model(_call(QUERY, sql="SELECT 1"), _say("x"))
    )
    assert not evaluate_case(case, bad).passed
