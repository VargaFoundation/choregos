# SPDX-License-Identifier: Apache-2.0
"""Batch model APIs (S4-09, ADR 0190): work nobody waits on, at half the price.

A batch is a set of independent requests submitted at once, answered within 24 hours (usually less)
at 50 % of the synchronous price on every token. Results come back in any order, each keyed by the
`custom_id` the caller chose: the caller maps them back, never by position.

`BatchBackend` is what a provider offers: `submit` once (the caller keeps the returned id), `poll`
as often as needed, `results` when the batch has ended, `cancel` to stop paying for the rest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

#: The discount a batch gets on every token (cache reads and writes included), Anthropic and OpenAI.
DISCOUNT = 0.5


class BatchError(RuntimeError):
    """The provider refused, or could not be reached; `status` is the HTTP status when there is one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class BatchRequest:
    """One request of a batch: a system prompt and one user message, answered as text."""

    custom_id: str
    system: str
    user: str
    max_tokens: int


@dataclass(frozen=True)
class BatchResult:
    """What one request came to: `succeeded` carries the text and the usage, anything else its reason."""

    custom_id: str
    status: Literal["succeeded", "errored", "canceled", "expired"]
    text: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class BatchStatus:
    id: str
    #: `in_progress` until every request has an outcome, then `ended` (cancelled batches end too)
    state: Literal["in_progress", "ended"]
    counts: dict[str, int] = field(default_factory=dict)


class BatchBackend(Protocol):
    name: str

    async def submit(self, model: str, requests: list[BatchRequest]) -> str: ...

    async def poll(self, batch_id: str) -> BatchStatus: ...

    async def results(self, batch_id: str) -> list[BatchResult]: ...

    async def cancel(self, batch_id: str) -> None: ...


def json_text(text: str) -> dict[str, Any] | None:
    """The JSON object a model answered, with or without a code fence around it; `None` when it is not
    one (a provider without a JSON mode may wrap it, or be cut by `max_tokens`)."""
    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        loaded = json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None
