# SPDX-License-Identifier: Apache-2.0
"""The fake of the batch API (ADR 0005, ADR 0190): in process, deterministic, scriptable.

A submitted batch stays `in_progress` for `polls_until_ended` polls, then `ended`. Without a script
each request answers one JSON object citing the first `=== DOCUMENT: <file> | ...` header of its text.
Scripting: `respond(request) -> text` replaces the answer, `outcomes[custom_id]` forces `errored`,
`canceled` or `expired`, `fail_submit` makes `submit` raise; `submitted` records every batch (model and
requests), `polls` and `cancelled` the calls. Results come back in reverse order on purpose: a caller
that trusts the position instead of `custom_id` fails against the fake.
Used under `CHOREGOS_FAKES=1` and by the tests.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from ..batch.base import BatchError, BatchRequest, BatchResult, BatchStatus

_HEADER = re.compile(r"=== DOCUMENT: (.+?) \|")


def default_answer(request: BatchRequest) -> str:
    found = _HEADER.search(request.user)
    document = found.group(1) if found else "unknown.md"
    return json.dumps(
        {
            "description": "Principles of the knowledge base; apply them and cite them.",
            "principles": [
                {
                    "title": f"Rule of {document}",
                    "statement": f"Follow what {document} states.",
                    "rationale": "Stated in the document.",
                    "sources": [{"document": document, "section": "1"}],
                }
            ],
        }
    )


@dataclass
class FakeBatches:
    name: str = "fake"
    polls_until_ended: int = 1
    respond: Callable[[BatchRequest], str] = default_answer
    outcomes: dict[str, Literal["errored", "canceled", "expired"]] = field(default_factory=dict)
    fail_submit: bool = False
    submitted: list[tuple[str, list[BatchRequest]]] = field(default_factory=list)
    polls: dict[str, int] = field(default_factory=dict)
    cancelled: list[str] = field(default_factory=list)

    async def submit(self, model: str, requests: list[BatchRequest]) -> str:
        if self.fail_submit:
            raise BatchError("fake: the provider refused the batch", 400)
        self.submitted.append((model, list(requests)))
        batch_id = f"fakebatch_{len(self.submitted)}"
        self.polls[batch_id] = 0
        return batch_id

    async def poll(self, batch_id: str) -> BatchStatus:
        if batch_id not in self.polls:
            raise BatchError("fake: no such batch", 404)
        self.polls[batch_id] += 1
        ended = batch_id in self.cancelled or self.polls[batch_id] > self.polls_until_ended
        return BatchStatus(batch_id, "ended" if ended else "in_progress")

    async def results(self, batch_id: str) -> list[BatchResult]:
        if batch_id not in self.polls:
            raise BatchError("fake: no such batch", 404)
        found: list[BatchResult] = []
        for request in reversed(self.submitted[int(batch_id.rsplit("_", 1)[1]) - 1][1]):
            if request.custom_id in self.outcomes:
                found.append(BatchResult(request.custom_id, self.outcomes[request.custom_id], error="fake"))
                continue
            text = self.respond(request)
            used = {"input_tokens": len(request.system + request.user) // 4, "output_tokens": len(text) // 4}
            found.append(BatchResult(request.custom_id, "succeeded", text, used))
        return found

    async def cancel(self, batch_id: str) -> None:
        self.cancelled.append(batch_id)
