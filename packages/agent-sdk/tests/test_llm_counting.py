"""Any framework's model client is counted: it is given `ctx.llm.http_client()` (S39-17e)."""

from __future__ import annotations

from pathlib import Path

import pytest
from choregos_agent_sdk import MaxTurnsExceeded
from choregos_agent_sdk.llm import Llm
from openai import AsyncOpenAI

from .conftest import RUN_KEY, Gateway
from .fakes import FakeConn, make_session


def _llm(gateway: Gateway, tmp_path: Path, max_turns: int | None, conn: FakeConn | None = None) -> Llm:
    session = make_session(tmp_path, conn, max_turns)
    env = {"OPENAI_BASE_URL": gateway.url, "OPENAI_API_KEY": RUN_KEY, "OPENAI_MODEL": "platform/standard"}
    return Llm(session, env)


async def test_a_raw_client_on_the_counting_transport_counts_and_is_journaled(
    gateway: Gateway, tmp_path: Path
) -> None:
    conn = FakeConn()
    llm = _llm(gateway, tmp_path, 5, conn)
    raw = AsyncOpenAI(
        base_url=gateway.url, api_key=RUN_KEY, http_client=llm.http_client()
    )  # what a framework does
    answer = await raw.chat.completions.create(
        model="platform/standard", messages=[{"role": "user", "content": "x"}]
    )
    assert answer.choices[0].message.content == "hi"
    assert llm._session.turns == 1
    assert [g["authorization"] for g in gateway.seen] == [f"Bearer {RUN_KEY}"]
    titles = [getattr(u, "title", None) for u in conn.updates]
    assert "llm platform/standard" in titles, "the runner's journal sees the model call"


async def test_the_call_past_max_turns_is_refused_before_it_leaves(gateway: Gateway, tmp_path: Path) -> None:
    llm = _llm(gateway, tmp_path, 1)
    http = llm.http_client()
    body = {"model": "m", "messages": []}
    assert (await http.post(f"{gateway.url}/chat/completions", json=body)).status_code == 200
    with pytest.raises(MaxTurnsExceeded):
        await http.post(f"{gateway.url}/chat/completions", json=body)
    assert len(gateway.seen) == 1, "the second request never reached the gateway"


async def test_a_framework_that_wraps_the_error_still_stops_the_step(
    gateway: Gateway, tmp_path: Path
) -> None:
    from choregos_agent_sdk.errors import sdk_error_in

    llm = _llm(gateway, tmp_path, 0)
    raw = AsyncOpenAI(base_url=gateway.url, api_key=RUN_KEY, http_client=llm.http_client(), max_retries=0)
    with pytest.raises(Exception) as raised:
        await raw.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}])
    assert isinstance(sdk_error_in(raised.value), MaxTurnsExceeded)
    assert gateway.seen == []


async def test_a_streamed_answer_passes_through_intact(gateway: Gateway, tmp_path: Path) -> None:
    llm = _llm(gateway, tmp_path, 3)
    stream = await llm.openai_client().chat.completions.create(
        model="m", messages=[{"role": "user", "content": "x"}], stream=True
    )
    text = "".join([chunk.choices[0].delta.content or "" async for chunk in stream if chunk.choices])
    assert text == "hello"
    assert llm._session.turns == 1


async def test_other_paths_are_not_counted(gateway: Gateway, tmp_path: Path) -> None:
    llm = _llm(gateway, tmp_path, 1)
    # The fake gateway answers any POST; only model paths are turns.
    await llm.http_client().post(f"{gateway.url}/embeddings", json={})
    assert llm._session.turns == 0


async def test_chat_uses_the_same_counted_client(gateway: Gateway, tmp_path: Path) -> None:
    llm = _llm(gateway, tmp_path, 1)
    assert (await llm.chat([{"role": "user", "content": "x"}])).text == "hi"
    with pytest.raises(MaxTurnsExceeded):
        await llm.chat([{"role": "user", "content": "y"}])
    assert llm._session.turns == 1
