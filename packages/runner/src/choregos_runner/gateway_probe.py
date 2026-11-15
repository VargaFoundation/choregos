# SPDX-License-Identifier: Apache-2.0
"""Asking the gateway why an agent's turn failed, when the agent did not say (S65-35, finding #1742).

`budget` reads a gateway refusal in what the agent relays: its prompt error, its error output, its
text. Some agents relay nothing of it. opencode turns a provider's error into a `None` stream and
ends its turn with `'NoneType' object has no attribute '__aiter__'`: the gateway's
`budget_exceeded` never reaches the run, which ended `agent_silencieux` with "check its access to
the model", and the workflow retried it.

When a turn ends in an error that names no refusal, the runner asks the gateway itself, once per
run, with the run's key: one model call of one token. A key the gateway refuses costs nothing (the
refusal comes before any model is called) and its answer names the refusal; a key it still serves
costs a one-token answer. A budget refusal ends the run with `reason="budget"`, which the workflow
never retries (ADR 0047); any other refusal is named in the run's summary.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from choregos_contracts import ModelRef

from .budget import gateway_budget_refusal

#: How long the runner waits for the gateway's answer: a diagnosis, never a delay.
PROBE_TIMEOUT_S = 15.0
#: What the runner journals once it has asked the gateway.
PROBE_EVENT = "gateway.probe"

Record = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class GatewayAnswer:
    """What the gateway answered: its status (0 when it could not be reached) and its error, in its
    own words."""

    status: int
    text: str

    @property
    def refused(self) -> bool:
        return self.status != 200

    @property
    def budget_refusal(self) -> str | None:
        """The gateway's budget refusal, read as `gateway_budget_refusal` reads any, or `None`."""
        if not self.refused:
            return None
        return gateway_budget_refusal(self.text.splitlines())

    def describe(self) -> str:
        """One line for a run's summary: what the gateway answers the run's key."""
        if not self.status:
            return f"the gateway could not be reached: {self.text[:200]}"
        return f"the gateway answers the run's key {self.status}" + (
            f": {self.text[:200]}" if self.text else ""
        )


def _error_text(response: httpx.Response) -> str:
    """The gateway's error as it wrote it: LiteLLM's `{"error": {"type", "message"}}` read as
    `<type>: <message>`, any other body as its text."""
    try:
        body: Any = response.json()
    except ValueError:
        return response.text.strip()[:500]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        kind, message = error.get("type"), error.get("message")
        text = f"{kind}: {message}" if kind and message else str(message or kind or "")
        if text:
            return text[:500]
    return json.dumps(body)[:500]


async def ask_the_gateway(
    model: ModelRef,
    key: str | None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = PROBE_TIMEOUT_S,
) -> GatewayAnswer:
    """One model call of one token with the run's key. Never raises: a gateway out of reach is an
    answer too."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {
        "model": model.litellm_model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as http:
            response = await http.post(
                f"{model.base_url.rstrip('/')}/chat/completions", json=body, headers=headers
            )
    except Exception as exc:  # a diagnosis must not fail the run it diagnoses
        return GatewayAnswer(0, f"{type(exc).__name__}: {exc}"[:300])
    if response.status_code == 200:
        return GatewayAnswer(200, "")
    return GatewayAnswer(response.status_code, _error_text(response))


class GatewayProbe:
    """The gateway asked once per run, after a turn that ended in an error naming no refusal.

    The first call asks and journals the answer (`gateway.probe`); later calls return the same
    answer without asking again: a run's repair turns meet the same gateway."""

    def __init__(
        self,
        model: ModelRef,
        key: str | None,
        record: Record,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.key = key
        self.record = record
        self.transport = transport
        self.answer: GatewayAnswer | None = None

    async def __call__(self) -> str | None:
        """The gateway's budget refusal of the run's key, or `None`."""
        if not self.model.base_url:
            return None  # a step without a model (a job) has no gateway to ask
        if self.answer is None:
            self.answer = await ask_the_gateway(self.model, self.key, transport=self.transport)
            await self.record(
                PROBE_EVENT,
                {
                    "status": self.answer.status,
                    "budget": self.answer.budget_refusal is not None,
                    "error": self.answer.text[:300],
                },
            )
        return self.answer.budget_refusal

    @property
    def failure(self) -> str | None:
        """What the gateway answered when it refused the key for another reason, or could not be
        reached; `None` when it was not asked, or served the key."""
        if self.answer is None or not self.answer.refused:
            return None
        return self.answer.describe()
