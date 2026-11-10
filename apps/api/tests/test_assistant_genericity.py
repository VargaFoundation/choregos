"""The genericity guard of assistants, dynamic half (S63-19, ADR 0133 point 7): the fixture plugin
`investment-assistant` installs and converses end to end on fakes through the public routes, with no code of
its own in the core. (The static half is `test_core_names_no_business_vocabulary.py`.)

The journey: the plugin is installed, two fake MCP connectors (news, market data) serve its needs, the
assistant is installed and bound until it is `ready`, a member of the audience opens a conversation and sends
one turn in which a scripted model reads a quote (whose `subject` is recorded as resolved) and proposes a note
on the instrument (a `user_subject` space whose resolver is `none`). It checks the answer, the journal, the
cost booked and the proposed fact. Removing a step's feature turns it red: unbound needs (no `ready`), the
audience (no conversation), the tool binding (the quote tool is not offered), the memory tools (no proposal),
the cost booking (no ledger row).

Not covered: the knowledge seed (`knowledge_search` over a copy of `investment-policy`) is S63-06's, which was
not on the branch when this story was written; `brokerage` and the other `user_consent` needs stay unbound.
"""

# ruff: noqa: F811 - a fixture imported from another module is redefined by the parameter that names it

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from .test_agent_sessions import Upstream, _ledger, upstream  # noqa: F401
from .test_assistant_install import (
    ORG,
    _bind,
    _install,
    _member,
    _plugin,
    offline,  # noqa: F401
)
from .test_assistant_rights import ME, OPEN, _as

ISIN = "FR0000120271"
AUDIENCE = "investors"
TOOLS = [
    {"name": n, "description": n, "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object"}}
    for n in ("search_news", "get_article", "get_quote", "get_history", "lookup_instrument")
]


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """One fake MCP server behind both connectors, `get_quote` scripted with a subject."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer(tools=[dict(t) for t in TOOLS], token="feed-key")
    server.structured["get_quote"] = {
        "subject": {"type": "instrument", "key": ISIN},
        "price": 61.4,
        "as_of": "2026-11-10T16:30:00Z",
        "source": "fake-exchange",
    }
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_FEED_KEY", "feed-key")
    yield server


async def _served(client: AsyncClient, name: str) -> None:
    body = {"name": name, "type": "mcp", "config": {"url": f"https://{name}.test/mcp"},
            "secret_refs": {"token": "env:CONNECTOR_TEST_FEED_KEY"}}  # fmt: skip
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    assert (await client.post(f"{ORG}/connectors/{name}/discover")).status_code == 200
    for tool in TOOLS:
        opened = await client.patch(
            f"{ORG}/connectors/{name}/operations/{tool['name']}", json={"policy": "allowed"}
        )
        assert opened.status_code == 200, opened.text


async def test_the_investment_assistant_converses_end_to_end(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,
    upstream: Upstream,
    sources: Any,
) -> None:
    from choregos_api.db.models import AgentSessionEvent, MemoryFact
    from choregos_api.db.session import session_scope

    await _member(client, "reader@varga.dev", [AUDIENCE])
    await _plugin(client)
    for need in ("news-feed", "markets"):
        await _served(client, need)
    installed = await _install(client, audience_groups=[AUDIENCE])
    assert installed.status_code == 201, installed.text
    assert installed.json()["status"] == "incomplete"
    assert (await _bind(client, "news", "news-feed")).json()["status"] == "incomplete"
    ready = await _bind(client, "market_data", "markets")
    assert ready.json()["status"] == "ready", ready.text
    readiness = await client.get(f"{ORG}/assistants/investment-assistant/readiness")
    assert readiness.json() == {"status": "ready", "items": []}

    reader = await _as(app, "reader@varga.dev")
    opened = await reader.post(OPEN, json={})
    assert opened.status_code == 201, opened.text
    conversation = opened.json()["id"]
    note = {"space": "instrument_notes", "subject": {"type": "instrument", "key": ISIN},
            "content": "Watching the dividend.", "reason": "the person asked"}  # fmt: skip
    upstream.model.call("market_data__get_quote", {"isin": ISIN}).call("memory_propose", note).say(
        "The quote is 61.4 as of 2026-11-10 [fake-exchange]. I noted that you watch it."
    )
    turn = await reader.post(f"{ME}/conversations/{conversation}/messages", json={"input": "How is it?"})
    assert turn.status_code == 200, turn.text
    assert "61.4" in turn.json()["output_text"]

    (_, first) = upstream.model.requests[0]
    offered = {tool["function"]["name"] for tool in first["tools"]}
    assert {"market_data__get_quote", "news__search_news", "memory_propose"} <= offered
    assert not any(n.startswith(("brokerage__", "orders__")) for n in offered), "unbound: not offered"
    assert sources.calls == [("get_quote", {"isin": ISIN})]
    (_, last) = upstream.model.requests[-1]
    results = [m["content"] for m in last["messages"] if m["role"] == "tool"]
    assert "fake-exchange" in results[0] and '"status": "proposed"' in results[1].replace('":"', '": "')

    async with session_scope(orgs="*") as session:
        rows = await session.execute(
            select(AgentSessionEvent.type)
            .where(AgentSessionEvent.session_id == conversation)
            .order_by(AgentSessionEvent.seq)
        )
        journal = list(rows.scalars())
        facts = list((await session.execute(select(MemoryFact))).scalars())
    assert sum("tool" in kind for kind in journal) >= 2, journal
    (fact,) = facts
    assert fact.status == "proposed" and fact.subject == ISIN and fact.proposed_by.startswith("assistant:")
    assert sum(row.cost_usd for row in await _ledger()) > 0, "the turn's cost was booked"
