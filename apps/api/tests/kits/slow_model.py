# SPDX-License-Identifier: Apache-2.0
"""Helpers shared by `test_conversation_limits` and its neighbours (tests import kits, not each other)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..test_agent_sessions import Upstream, _agent, _key_rows, _ledger, upstream  # noqa: F401


class Slow(httpx.AsyncBaseTransport):
    """The upstream, with the model calls from the `after`-th on waiting `delay` seconds once they were
    booked on their key (so a call cancelled in flight has spent)."""

    def __init__(self, model: Upstream, *, after: int = 0, delay: float = 30) -> None:
        self.model, self.after, self.delay = model, after, delay
        self.calls = 0
        self.waiting = asyncio.Event()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        side = bool((json.loads(request.content).get("metadata") or {}).get("purpose"))
        self.calls += 0 if side else 1
        answer = self.model.handle(request)
        if self.calls > self.after and not side:
            self.waiting.set()
            await asyncio.sleep(self.delay)
        return answer


def _slow(model: Upstream, **terms: Any) -> Slow:
    from choregos_api.services.agent_sessions import set_model_transport

    transport = Slow(model, **terms)
    set_model_transport(transport)
    return transport
