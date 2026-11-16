# SPDX-License-Identifier: Apache-2.0
"""A cancel that reaches another API replica stops the turn (S63-23, ADR 0134).

Two ASGI applications share one PostgreSQL. The turn runs under the first; the cancel is sent to the second,
which is given an empty in-process registry as a replica in another process would have. The turn must stop,
its key be revoked, its spend booked, and the tool calls already made stay journaled.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import SUITE_ON_POSTGRES, login, without_postgres
from .kits.conversations import ME, _events, _open, _record, _say
from .kits.slow_model import _slow
from .test_agent_sessions import Upstream, _agent, _key_rows, _ledger, upstream  # noqa: F401

pytestmark = without_postgres


async def _second_replica(email: str) -> AsyncClient:
    from choregos_api.main import create_app

    http = AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test")
    await login(http, email)
    return http


@pytest.mark.skipif(
    not SUITE_ON_POSTGRES,
    reason="the cross-replica cancel exists when the application runs on PostgreSQL: CI's PostgreSQL pass "
    "(`CHOREGOS_TEST_SUITE_ON_POSTGRES=1`) runs it",
)
async def test_a_cancel_sent_to_another_replica_stops_the_turn(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.services import conversation_turns

    await _agent(client)
    upstream.model.call("lookup", {"order": 42}).say("never read")
    transport = _slow(upstream, after=1)
    conversation = await _open("varga", admin)
    turn = asyncio.ensure_future(_say(client, conversation, "Where is order 42?"))
    await asyncio.wait_for(transport.waiting.wait(), 10)
    # Another replica sees the turn by its committed lease: wait until it is there, as a real second
    # request would only come after it (on a loaded runner the model may be reached first).
    for _ in range(100):
        if (await _record(conversation)).turn_lease_until is not None:
            break
        await asyncio.sleep(0.05)
    assert (await _record(conversation)).turn_lease_until is not None, "the running turn holds a lease"

    # The cancel reaches a replica that does not run the turn: the registry is per process.
    monkeypatch.setattr(conversation_turns, "_RUNNING", {})
    second = await _second_replica(admin)
    try:
        cancelled = await second.post(f"{ME}/{conversation}/cancel")
        assert cancelled.status_code == 200, cancelled.text
    finally:
        await second.aclose()

    answer = await asyncio.wait_for(turn, 10)
    assert answer.json()["status"] == "failed" and answer.json()["error"]["code"] == "cancelled"
    assert upstream.gateway.keys == {} and len(upstream.gateway.revoked) == 1, "the key is revoked"
    rows = await _key_rows()
    assert len(rows) == 1 and rows[0].revoked and rows[0].spend_usd > 0
    lines = [entry for entry in await _ledger() if entry.session_id == conversation]
    assert len(lines) == 1 and lines[0].cost_usd == pytest.approx(rows[0].spend_usd)
    events = await _events(conversation)
    assert "prompt.tool_refused" in events, "the tool call already made stays journaled"
    assert "turn.cancelled" in events and "turn.ended" in events
    record = await _record(conversation)
    assert record.turn_lease_until is None and record.turn_cancel_requested_at is None


async def test_a_cancel_with_no_turn_running_is_a_conflict_on_any_replica(
    client: AsyncClient,
    admin: str,
    project: dict[str, Any],
    upstream: Upstream,  # noqa: F811
) -> None:
    await _agent(client)
    conversation = await _open("varga", admin)
    second = await _second_replica(admin)
    try:
        assert (await second.post(f"{ME}/{conversation}/cancel")).status_code == 409
    finally:
        await second.aclose()
    assert (await _record(conversation)).turn_cancel_requested_at is None
