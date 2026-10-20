"""A real realtime model through the real LiteLLM, priced by the voice relay (S41-15, ADR 0080 §10).

The fakes prove the arithmetic on a usage the test wrote. What only a real model proves: that its
`response.done` reports the usage `response_cost` reads (the split in text, audio and cached), that
the gateway lists the audio prices a voice session is measured at, and that a virtual key opens the
gateway's realtime socket. The gateway's own count is printed beside the relay's, never asserted
equal: it is the figure S41-15 stopped booking.

    CHOREGOS_LIVE_LITELLM_URL=http://127.0.0.1:4000 CHOREGOS_LIVE_LITELLM_KEY=sk-… \
    CHOREGOS_LIVE_REALTIME_MODEL=openai/gpt-realtime \
    uv run pytest tests/live/test_realtime_live.py -m live -s
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from choregos_adapters.gateway.litellm import LiteLlmGateway
from choregos_api.services.agent_realtime import WebsocketsConnector, realtime_url
from choregos_api.services.realtime_spend import realtime_prices, response_cost, session_cap

from .conftest import require

pytestmark = pytest.mark.live

RUN = uuid4().hex[:8]


@pytest.fixture
async def gateway() -> AsyncIterator[LiteLlmGateway]:
    env = require("CHOREGOS_LIVE_LITELLM_URL", "CHOREGOS_LIVE_LITELLM_KEY", "CHOREGOS_LIVE_REALTIME_MODEL")
    adapter = LiteLlmGateway(
        base_url=env["CHOREGOS_LIVE_LITELLM_URL"], master_key=env["CHOREGOS_LIVE_LITELLM_KEY"]
    )
    try:
        yield adapter
    finally:
        await adapter.aclose()


async def _until(link: Any, type_: str, timeout_s: float = 60) -> dict[str, Any]:
    async with asyncio.timeout(timeout_s):
        while True:
            event = json.loads(await link.recv())
            assert event.get("type") != "error", event
            if event.get("type") == type_:
                return dict(event)


async def test_a_real_realtime_response_is_priced_by_the_relay(gateway: LiteLlmGateway) -> None:
    env = require("CHOREGOS_LIVE_LITELLM_URL", "CHOREGOS_LIVE_REALTIME_MODEL")
    name = env["CHOREGOS_LIVE_REALTIME_MODEL"]
    (model,) = [m for m in await gateway.list_models() if name in (m.model_name, m.litellm_model)]
    prices = realtime_prices(model, None)
    assert prices is not None, f"the gateway gives `{name}` no audio price: a voice session would not open"

    key = await gateway.mint_key(
        {"session_id": f"live-{RUN}", "agent": "live-voice"}, budget_usd=0.25, ttl_s=600, models=[name]
    )
    link = await WebsocketsConnector().connect(
        realtime_url(env["CHOREGOS_LIVE_LITELLM_URL"], name), {"Authorization": f"Bearer {key.key}"}
    )
    try:
        await link.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {"type": "realtime", "instructions": "Answer in one word.", "voice": "verse"},
                }
            )
        )
        await link.send(json.dumps({"type": "response.create"}))
        done = await _until(link, "response.done")
    finally:
        await link.close()
        await gateway.revoke(key.key_id)

    usage = done["response"]["usage"]
    cost = response_cost(usage, prices)
    assert cost.tokens_in == usage["input_tokens"] and cost.tokens_out == usage["output_tokens"]
    assert "audio_tokens" in (usage.get("output_token_details") or {}), "the split the relay prices from"
    assert cost.cost_usd > 0
    assert session_cap(cost.cost_usd / 2, None, 0, 60) < cost.cost_usd, "a cap below it would have cut"
    readback = await gateway.spend(key.key_id)
    print(f"\nrelay: {cost.cost_usd:.6f} USD for {usage}; gateway: {readback.cost_usd:.6f} USD")
