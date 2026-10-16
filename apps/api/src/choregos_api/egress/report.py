# SPDX-License-Identifier: Apache-2.0
"""What the proxy tells the API: cumulative counters per (run, host, port, decision, reason).

The counters only grow and are sent as running totals, so a replayed or retried batch rewrites the
same numbers (`POST /internal/runs/{id}/network/decisions` replaces, it never adds). A failed post
is logged and tried again at the next flush; it never blocks traffic.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
import structlog

log = structlog.get_logger("choregos.egress")


class DecisionSink(Protocol):
    async def post(self, run_id: str, token: str, decisions: list[dict[str, Any]]) -> None: ...


class HttpDecisionSink:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.AsyncClient(timeout=10)

    async def post(self, run_id: str, token: str, decisions: list[dict[str, Any]]) -> None:
        response = await self.client.post(
            f"{self.base_url}/internal/runs/{run_id}/network/decisions",
            headers={"Authorization": f"Bearer {token}"},
            json={"decisions": decisions},
        )
        response.raise_for_status()

    async def aclose(self) -> None:
        await self.client.aclose()


class FakeDecisionSink:
    """Records what would have been posted; `fail` scripts an API that refuses."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, list[dict[str, Any]]]] = []
        self.fail = False

    async def post(self, run_id: str, token: str, decisions: list[dict[str, Any]]) -> None:
        if self.fail:
            raise httpx.ConnectError("scripted")
        self.posts.append((run_id, [dict(d) for d in decisions]))


@dataclass
class _Row:
    count: int = 0
    bytes_up: int = 0
    bytes_down: int = 0
    first_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class _Run:
    token: str
    rows: dict[tuple[str, int, str, str], _Row] = field(default_factory=dict)
    dirty: bool = False
    touched: float = 0.0


class Reporter:
    def __init__(self, sink: DecisionSink, clock: Callable[[], float] = time.monotonic) -> None:
        self.sink = sink
        self.clock = clock
        self._runs: dict[str, _Run] = {}

    def record(  # noqa: PLR0913
        self,
        run_id: str,
        token: str,
        host: str,
        port: int,
        decision: str,
        reason: str,
        *,
        count: int = 1,
        up: int = 0,
        down: int = 0,
    ) -> None:
        run = self._runs.setdefault(run_id, _Run(token=token))
        run.token = token
        row = run.rows.setdefault((host, port, decision, reason), _Row())
        row.count += count
        row.bytes_up += up
        row.bytes_down += down
        row.last_at = datetime.now(UTC)
        run.dirty = True
        run.touched = self.clock()

    def _payload(self, run: _Run) -> list[dict[str, Any]]:
        return [
            {
                "layer": "proxy",
                "host": host,
                "port": port,
                "decision": decision,
                "reason": reason[:200],
                "count": max(1, row.count),
                "bytes_up": row.bytes_up,
                "bytes_down": row.bytes_down,
                "first_at": row.first_at.isoformat(),
                "last_at": row.last_at.isoformat(),
            }
            for (host, port, decision, reason), row in run.rows.items()
        ]

    async def flush(self) -> int:
        """Post every run that changed since the last flush; returns how many runs were posted."""
        posted = 0
        for run_id, run in list(self._runs.items()):
            if not run.dirty:
                continue
            batch = self._payload(run)
            try:
                for start in range(0, len(batch), 500):
                    await self.sink.post(run_id, run.token, batch[start : start + 500])
            except Exception as error:  # reporting must never block or kill the traffic
                log.warning("network decisions not posted", run_id=run_id, error=str(error)[:200])
                continue
            run.dirty = False
            posted += 1
        idle = self.clock() - 900
        for run_id in [r for r, run in self._runs.items() if not run.dirty and run.touched < idle]:
            del self._runs[run_id]
        return posted
