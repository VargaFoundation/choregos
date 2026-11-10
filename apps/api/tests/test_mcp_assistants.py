"""The MCP door talks to assistants (S63-11, ADR 0139).

An assistant installed for an audience is a tool of `/mcp` and a connector of its own
(`/mcp/assistants/{org}:{installation}`, with the manifest's prompts). A turn is the durable conversation of
S63-04, in the caller's personal space; one that outlasts the wait answers `working` and is read back with
`get_conversation`. Someone outside the audience neither sees the assistant nor reaches its connector.
The model is the fake of `test_agent_sessions.py`, slowed down where the wait is the point.
"""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from httpx import AsyncClient

from .agent_scopes_kit import add_member, as_user
from .kits.assistant_door import ORG, connector, idp, idp_token, install_plugin, offline  # noqa: F401
from .kits.mcp_assistants import (
    _call,
    _names,
    _rpc,
)
from .test_agent_sessions import Upstream, upstream  # noqa: F401

INVESTORS = "grp-investors"
CONNECTOR = "/mcp/assistants/varga:investment-assistant"
NEWS = ("search_news", "get_article")
MARKET = ("get_quote", "get_history", "lookup_instrument")


async def _person(client: AsyncClient, email: str, groups: list[str]) -> str:
    """A member with these groups, and a read-only MCP token of theirs."""
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await add_member(client, email, "developer")
    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = groups
    await as_user(client, email)
    created = await client.post("/api/v1/me/tokens", json={"name": "mcp", "scopes": ["mcp:read"]})
    assert created.status_code == 201, created.text
    return str(created.json()["token"])


@pytest.fixture
async def installed(client: AsyncClient, org: str, offline: Path) -> str:  # noqa: F811
    """A ready investment assistant for `grp-investors`; returns the investor's token."""
    await install_plugin(client)
    created = await client.post(
        f"{ORG}/assistants",
        json={
            "plugin": "investment-assistant",
            "assistant": "investment-assistant",
            "config": {"house_name": "Varga"},
            "audience_groups": [INVESTORS],
        },
    )
    assert created.status_code == 201, created.text
    await connector("news-feed", NEWS)
    await connector("markets", MARKET)
    for need, name in (("news", "news-feed"), ("market_data", "markets")):
        ready = await client.put(
            f"{ORG}/assistants/investment-assistant/bindings/{need}", json={"connector": name}
        )
    assert ready.json()["status"] == "ready", ready.text
    return await _person(client, "ines@varga.dev", [INVESTORS])


def _wait(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_MCP_ASSISTANT_WAIT_S", str(seconds))
    reset_settings_cache()


def _slow(upstream: Upstream, delay: float) -> None:  # noqa: F811
    """The model answers after `delay` seconds."""
    from choregos_api.services.agent_sessions import set_model_transport

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        return upstream.handle(request)

    set_model_transport(httpx.MockTransport(handler))


# ───────────────────────── the door's tools ─────────────────────────


async def test_a_person_of_the_audience_converses_through_the_door(
    client: AsyncClient,
    installed: str,
    upstream: Upstream,  # noqa: F811
) -> None:
    upstream.model.say("The index rose 1.2 % today.").say("Because of the rate decision.")
    assert {"list_assistants", "ask_assistant", "get_conversation"} <= await _names(client, installed)
    listed = await _call(client, installed, "list_assistants", {})
    assert [a["assistant"] for a in listed["assistants"]] == ["varga:investment-assistant"]
    assert listed["assistants"][0]["connector_path"] == CONNECTOR

    first = await _call(
        client, installed, "ask_assistant", {"assistant": "varga:investment-assistant", "message": "Why up?"}
    )
    assert first["status"] == "completed" and first["text"] == "The index rose 1.2 % today."
    assert first["citations"] == [] and first["pending_actions"] == [] and first["missing_accounts"] == []
    second = await _call(
        client,
        installed,
        "ask_assistant",
        {
            "assistant": "investment-assistant",
            "message": "And why?",
            "conversation_id": first["conversation_id"],
        },
    )
    assert second["conversation_id"] == first["conversation_id"]
    assert second["text"] == "Because of the rate decision."

    # It is the `/me/conversations` object of the person, in their personal space, tied to the installation.
    await as_user(client, "ines@varga.dev")
    read = (await client.get(f"/api/v1/me/conversations/{first['conversation_id']}")).json()
    assert read["turns"] == 2 and read["installation_id"] is not None
    assert [m["content"] for m in read["messages"]][-1] == "Because of the rate decision."
    again = await _call(client, installed, "get_conversation", {"conversation_id": first["conversation_id"]})
    assert again["status"] == "completed" and again["text"] == "Because of the rate decision."


async def test_a_turn_longer_than_the_wait_answers_working_then_the_answer(
    client: AsyncClient,
    installed: str,
    upstream: Upstream,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream.model.say("Finally: 42.")
    _slow(upstream, 0.6)
    _wait(monkeypatch, 0.05)
    started = await _call(
        client, installed, "ask_assistant", {"assistant": "varga:investment-assistant", "message": "Slow?"}
    )
    assert started["status"] == "working" and started["text"] == ""
    description = next(
        t["description"]
        for t in (await _rpc(client, installed, "tools/list")).json()["result"]["tools"]
        if t["name"] == "ask_assistant"
    )
    assert "get_conversation" in description

    # While it runs, another message in the same conversation is refused, not queued.
    busy = await _rpc(
        client,
        installed,
        "tools/call",
        {
            "name": "ask_assistant",
            "arguments": {
                "assistant": "varga:investment-assistant",
                "message": "More",
                "conversation_id": started["conversation_id"],
            },
        },
    )
    assert busy.json()["result"]["isError"] is True

    polled = started
    for _ in range(100):
        polled = await _call(
            client, installed, "get_conversation", {"conversation_id": started["conversation_id"]}
        )
        if polled["status"] != "working":
            break
        await asyncio.sleep(0.05)
    assert polled["status"] == "completed" and polled["text"] == "Finally: 42."
    # Nobody else reads it.
    other = await _person(client, "omar@varga.dev", [INVESTORS])
    refused = await _rpc(
        client,
        other,
        "tools/call",
        {"name": "get_conversation", "arguments": {"conversation_id": started["conversation_id"]}},
    )
    assert refused.json()["result"]["isError"] is True


async def test_someone_outside_the_audience_does_not_see_the_assistant(
    client: AsyncClient, installed: str
) -> None:
    outsider = await _person(client, "paul@varga.dev", ["grp-sales"])
    names = await _names(client, outsider)
    assert not names & {"list_assistants", "ask_assistant", "get_conversation"}
    refused = await _rpc(client, outsider, "tools/call", {"name": "list_assistants", "arguments": {}})
    assert refused.json()["error"]["code"] == -32602, "an unannounced tool does not exist for them"
    assert (await _rpc(client, outsider, "tools/list", path=CONNECTOR)).status_code == 404
    assert (await _rpc(client, outsider, "prompts/list", path=CONNECTOR)).status_code == 404
    # ... while the audience's own person reaches it.
    assert (await _rpc(client, installed, "tools/list", path=CONNECTOR)).status_code == 200


async def test_an_assistant_that_is_not_ready_is_not_announced(client: AsyncClient, installed: str) -> None:
    from choregos_api.db.models import AssistantInstallation
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        from sqlalchemy import select

        row = (await session.execute(select(AssistantInstallation))).scalar_one()
        row.status = "incomplete"
    assert "list_assistants" not in await _names(client, installed)
    assert (await _rpc(client, installed, "tools/list", path=CONNECTOR)).status_code == 404


# ───────────────────────── the assistant as a connector ─────────────────────────


async def test_the_connector_has_its_own_tools_and_prompts(
    client: AsyncClient,
    installed: str,
    upstream: Upstream,  # noqa: F811
) -> None:
    initialised = (await _rpc(client, installed, "initialize", {}, CONNECTOR)).json()["result"]
    assert "prompts" in initialised["capabilities"]
    plain = (await _rpc(client, installed, "initialize", {})).json()["result"]
    assert "prompts" not in plain["capabilities"], "the organisation's door has no prompts"
    assert await _names(client, installed, CONNECTOR) == {"ask", "get_conversation"}

    prompts = (await _rpc(client, installed, "prompts/list", path=CONNECTOR)).json()["result"]["prompts"]
    assert prompts == [
        {
            "name": "explain_move",
            "description": "Explain why an instrument moved today.",
            "arguments": [{"name": "instrument", "required": True}],
        }
    ]
    got = await _rpc(
        client,
        installed,
        "prompts/get",
        {"name": "explain_move", "arguments": {"instrument": "ACME"}},
        CONNECTOR,
    )
    message = got.json()["result"]["messages"][0]
    assert message == {"role": "user", "content": {"type": "text", "text": "Why did ACME move today?"}}
    missing = await _rpc(
        client, installed, "prompts/get", {"name": "explain_move", "arguments": {}}, CONNECTOR
    )
    assert missing.json()["error"]["code"] == -32602
    unknown = await _rpc(client, installed, "prompts/get", {"name": "nope"}, CONNECTOR)
    assert unknown.json()["error"]["code"] == -32602
    on_the_door = await _rpc(client, installed, "prompts/list")
    assert on_the_door.json()["error"]["code"] == -32601

    upstream.model.say("It fell on a profit warning.")
    answer = await _call(client, installed, "ask", {"message": message["content"]["text"]}, CONNECTOR)
    assert answer["status"] == "completed" and answer["text"] == "It fell on a profit warning."


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
async def served(app: Any) -> AsyncIterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


async def test_the_reference_client_converses_and_reads_the_prompts(
    served: str,
    client: AsyncClient,
    installed: str,
    upstream: Upstream,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    upstream.model.say("Rates are on hold.")
    _slow(upstream, 0.5)
    _wait(monkeypatch, 0.05)
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {installed}"})
    async with (
        http,
        streamable_http_client(f"{served}{CONNECTOR}", http_client=http) as stream,
        ClientSession(stream[0], stream[1]) as session,
    ):
        await session.initialize()
        prompts = await session.list_prompts()
        assert [p.name for p in prompts.prompts] == ["explain_move"]
        rendered = await session.get_prompt("explain_move", {"instrument": "ACME"})
        assert rendered.messages[0].content.text == "Why did ACME move today?"  # type: ignore[union-attr]
        started = await session.call_tool("ask", {"message": "Rates?"})
        assert not started.is_error and started.structured_content is not None
        assert started.structured_content["status"] == "working"
        conversation = started.structured_content["conversation_id"]
        result = started
        for _ in range(100):
            result = await session.call_tool("get_conversation", {"conversation_id": conversation})
            assert result.structured_content is not None
            if result.structured_content["status"] != "working":
                break
            await asyncio.sleep(0.05)
        assert result.structured_content is not None
        assert result.structured_content["status"] == "completed"
        assert result.structured_content["text"] == "Rates are on hold."


# ───────────────────────── OAuth ─────────────────────────


async def test_an_oauth_client_makes_a_turn(
    idp: Any,  # noqa: F811
    client: AsyncClient,
    installed: str,
    upstream: Upstream,  # noqa: F811
) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == "ines@varga.dev"))).scalar_one()
        user.oidc_sub = "sub-ines"
    client.cookies.clear()
    token = idp_token(idp, sub="sub-ines")
    upstream.model.say("Hello from the assistant.")
    answer = await _call(
        client, token, "ask_assistant", {"assistant": "varga:investment-assistant", "message": "Hi"}
    )
    assert answer["status"] == "completed" and answer["text"] == "Hello from the assistant."


@pytest.fixture(autouse=True)
def _reset_turns() -> Iterator[None]:
    from choregos_api.mcp import assistants

    yield
    assistants._TURNS.clear()
    assistants._OUTCOMES.clear()
