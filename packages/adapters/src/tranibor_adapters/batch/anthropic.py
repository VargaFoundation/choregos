# SPDX-License-Identifier: Apache-2.0
"""Anthropic Message Batches: `POST /v1/messages/batches`, 50 % off, results within 24 h.

Endpoints: `POST /v1/messages/batches` (submit), `GET /v1/messages/batches/{id}` (poll, the batch is
`ended` when `processing_status` says so), `GET /v1/messages/batches/{id}/results` (JSON lines, in any
order, keyed by `custom_id`), `POST /v1/messages/batches/{id}/cancel`. The key goes in `x-api-key`.

The LiteLLM gateway is not used (ADR 0190): its batch endpoints speak the OpenAI shape, and a real
Anthropic batch through it could not be shown here.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .base import BatchError, BatchRequest, BatchResult, BatchStatus

VERSION = "2023-06-01"
#: Anthropic's own ceiling for one batch (it is 256 MB too).
MAX_REQUESTS = 100_000


class AnthropicBatches:
    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.anthropic.com",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key, self._base = api_key, base_url.rstrip("/")
        self._timeout, self._transport = timeout, transport

    async def _send(self, method: str, path: str, **options: Any) -> httpx.Response:
        if not self._key:
            raise BatchError("no Anthropic API key: the batch has no provider to call")
        headers = {"x-api-key": self._key, "anthropic-version": VERSION}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.request(method, self._base + path, headers=headers, **options)
        except httpx.HTTPError as exc:
            raise BatchError(f"Anthropic batches unreachable: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise BatchError(
                f"Anthropic batches answered {response.status_code}: {response.text[:300]}",
                response.status_code,
            )
        return response

    async def submit(self, model: str, requests: list[BatchRequest]) -> str:
        if not requests or len(requests) > MAX_REQUESTS:
            raise BatchError(f"a batch holds 1 to {MAX_REQUESTS} requests, not {len(requests)}")
        body = {
            "requests": [
                {
                    "custom_id": r.custom_id,
                    "params": {
                        "model": model,
                        "max_tokens": r.max_tokens,
                        "system": r.system,
                        "messages": [{"role": "user", "content": r.user}],
                    },
                }
                for r in requests
            ]
        }
        batch_id = (await self._send("POST", "/v1/messages/batches", json=body)).json().get("id")
        if not batch_id:
            raise BatchError("Anthropic batches answered no batch id")
        return str(batch_id)

    async def poll(self, batch_id: str) -> BatchStatus:
        body = (await self._send("GET", f"/v1/messages/batches/{batch_id}")).json()
        ended = body.get("processing_status") == "ended"
        counts = {k: v for k, v in (body.get("request_counts") or {}).items() if isinstance(v, int)}
        return BatchStatus(batch_id, "ended" if ended else "in_progress", counts)

    async def results(self, batch_id: str) -> list[BatchResult]:
        response = await self._send("GET", f"/v1/messages/batches/{batch_id}/results")
        found: list[BatchResult] = []
        for line in response.text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                outcome = row["result"]
                custom_id = str(row["custom_id"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            kind = outcome.get("type")
            if kind == "succeeded":
                message = outcome.get("message") or {}
                blocks = message.get("content") or []
                text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
                used = message.get("usage") or {}
                usage = {
                    key: int(used.get(key) or 0)
                    for key in (
                        "input_tokens",
                        "output_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    )
                }
                found.append(BatchResult(custom_id, "succeeded", text, usage))
            elif kind in ("errored", "canceled", "expired"):
                error = outcome.get("error") or {}
                reason = (error.get("error") or error) if isinstance(error, dict) else {}
                found.append(BatchResult(custom_id, kind, error=str(reason.get("message") or kind)))
        return found

    async def cancel(self, batch_id: str) -> None:
        await self._send("POST", f"/v1/messages/batches/{batch_id}/cancel")
