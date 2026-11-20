# SPDX-License-Identifier: Apache-2.0
"""The gateway's batch API: LiteLLM's OpenAI-compatible Files + Batches endpoints, 50 % off upstream.

Through the platform gateway (ADR 0004 pricing, the gateway key, the gateway's egress), never to a
provider directly (ADR 0190). The wire, as LiteLLM v1.105.5 serves it:

- `POST /v1/files` (multipart: `purpose=batch`, `target_model_names=<alias>`, a `.jsonl` of
  `{custom_id, method, url, body}` rows) keeps the file against the alias; the router rewrites each row's
  `body.model` to the model the alias resolves to, so the rows carry the alias and nothing else;
- `POST /v1/batches` (`input_file_id`, `endpoint`, `completion_window`): the model comes from the file;
- `GET /v1/batches/{id}`: `status`, `request_counts`, `output_file_id`, `error_file_id`;
- `GET /v1/files/{output_file_id}/content`: JSON lines `{custom_id, response: {status_code, body}, error}`,
  in any order; `POST /v1/batches/{id}/cancel`.

Upstreams without a batch API answer an error on upload or submit: `BatchError`, the caller goes
synchronous.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from .base import BatchError, BatchRequest, BatchResult, BatchStatus

ENDPOINT = "/v1/chat/completions"
#: OpenAI's own ceiling for one batch input file.
MAX_REQUESTS = 50_000
_ENDED = {"completed", "failed", "expired", "cancelled"}


class GatewayBatches:
    name = "gateway"

    def __init__(
        self,
        base_url: str,
        key: str,
        *,
        timeout: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base, self._key = base_url.rstrip("/"), key
        self._timeout, self._transport = timeout, transport

    async def _send(self, method: str, path: str, **options: Any) -> httpx.Response:
        if not self._key:
            raise BatchError("no gateway key: the batch has no gateway to call")
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.request(
                    method, self._base + path, headers={"Authorization": f"Bearer {self._key}"}, **options
                )
        except httpx.HTTPError as exc:
            raise BatchError(f"the gateway is unreachable: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise BatchError(
                f"the gateway answered {response.status_code} on {method} {path.split('?', maxsplit=1)[0]}: "
                f"{response.text[:300]}",
                response.status_code,
            )
        return response

    async def submit(self, model: str, requests: list[BatchRequest]) -> str:
        if not requests or len(requests) > MAX_REQUESTS:
            raise BatchError(f"a batch holds 1 to {MAX_REQUESTS} requests, not {len(requests)}")
        rows = [
            {
                "custom_id": r.custom_id,
                "method": "POST",
                "url": ENDPOINT,
                "body": {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": r.system},
                        {"role": "user", "content": r.user},
                    ],
                    "max_tokens": r.max_tokens,
                    "response_format": {"type": "json_object"},
                },
            }
            for r in requests
        ]
        payload = "\n".join(json.dumps(row) for row in rows) + "\n"
        uploaded = await self._send(
            "POST",
            "/v1/files",
            data={"purpose": "batch", "target_model_names": model},
            files={"file": ("batch.jsonl", payload.encode(), "application/jsonl")},
        )
        file_id = uploaded.json().get("id")
        if not file_id:
            raise BatchError("the gateway answered no file id")
        created = await self._send(
            "POST",
            "/v1/batches",
            json={"input_file_id": file_id, "endpoint": ENDPOINT, "completion_window": "24h"},
        )
        batch_id = created.json().get("id")
        if not batch_id:
            raise BatchError("the gateway answered no batch id")
        return str(batch_id)

    async def _batch(self, batch_id: str) -> dict[str, Any]:
        body = (await self._send("GET", f"/v1/batches/{quote(batch_id, safe='')}")).json()
        return body if isinstance(body, dict) else {}

    async def poll(self, batch_id: str) -> BatchStatus:
        body = await self._batch(batch_id)
        status = body.get("status")
        counts = {k: v for k, v in (body.get("request_counts") or {}).items() if isinstance(v, int)}
        # A completed batch can show no output file for a moment (LiteLLM prices only once it has one):
        # wait for it, unless the counts prove no request succeeded.
        done = status in _ENDED and not (
            status == "completed" and not body.get("output_file_id") and counts.get("completed", 0) > 0
        )
        return BatchStatus(batch_id, "ended" if done else "in_progress", counts)

    async def _lines(self, file_id: str) -> list[dict[str, Any]]:
        text = (await self._send("GET", f"/v1/files/{quote(file_id, safe='')}/content")).text
        rows: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("custom_id") is not None:
                rows.append(row)
        return rows

    async def results(self, batch_id: str) -> list[BatchResult]:
        batch = await self._batch(batch_id)
        found: dict[str, BatchResult] = {}
        for file_key in ("error_file_id", "output_file_id"):  # the output wins over an error of the same id
            if batch.get(file_key):
                for row in await self._lines(str(batch[file_key])):
                    found[str(row["custom_id"])] = _result(row)
        return list(found.values())

    async def cancel(self, batch_id: str) -> None:
        await self._send("POST", f"/v1/batches/{quote(batch_id, safe='')}/cancel")


def _result(row: dict[str, Any]) -> BatchResult:
    custom_id = str(row["custom_id"])
    response = row.get("response") or {}
    body = response.get("body") if isinstance(response, dict) else None
    status = response.get("status_code") if isinstance(response, dict) else None
    if status == 200 and isinstance(body, dict) and body.get("choices"):
        message = (body["choices"][0] or {}).get("message") or {}
        used = body.get("usage") or {}
        usage = {
            "input_tokens": int(used.get("prompt_tokens") or 0),
            "output_tokens": int(used.get("completion_tokens") or 0),
        }
        return BatchResult(custom_id, "succeeded", str(message.get("content") or ""), usage)
    error = row.get("error")
    if not error and isinstance(body, dict):
        error = body.get("error")
    reason = error.get("message") if isinstance(error, dict) else error
    expired = isinstance(error, dict) and error.get("code") == "batch_expired"
    return BatchResult(
        custom_id, "expired" if expired else "errored", error=str(reason or f"status {status}")
    )
