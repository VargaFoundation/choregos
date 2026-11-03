# SPDX-License-Identifier: Apache-2.0
"""`DecisionClient` over HTTP: a decision step's call to the gateway's `POST /v1/systemone`.

The platform never calls a decision model directly (ADR 0121 §2): it calls the project's gateway,
with the virtual key minted for the step, so that the cost is counted and capped at the source. The
wire and how it maps to a decision step's questions are in `choregos_adapters.decisions`.
"""

from __future__ import annotations

import httpx

from ..decisions import (
    DecisionCall,
    DecisionReply,
    DecisionUnreachableError,
    InvalidDecisionError,
    read_systemone_answers,
    systemone_request,
    systemone_url,
)


class SystemOneClient:
    """Asks a decision model through the gateway; one HTTP call per decision."""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        # Injected by the tests (a `MockTransport`); otherwise one client per call: a decision step
        # is one call, made from an activity that builds its adapters afresh.
        self._client = client

    async def decide(self, call: DecisionCall) -> DecisionReply:
        url = systemone_url(call.base_url)
        body = systemone_request(call.model, call.state, call.questions)
        headers = {"Authorization": f"Bearer {call.key}"}
        try:
            if self._client is not None:
                response = await self._client.post(url, json=body, headers=headers, timeout=call.timeout_s)
            else:
                async with httpx.AsyncClient(timeout=call.timeout_s) as client:
                    response = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as error:
            raise DecisionUnreachableError(f"POST {url}: {type(error).__name__} {error}"[:500]) from error
        if response.status_code >= 400:
            raise DecisionUnreachableError(
                f"POST {url} → {response.status_code}: {response.text[:300]}",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as error:
            raise InvalidDecisionError(
                f"the gateway's answer is not JSON: {response.text[:200]!r}"
            ) from error
        return read_systemone_answers(call.questions, data)


__all__ = ["SystemOneClient"]
