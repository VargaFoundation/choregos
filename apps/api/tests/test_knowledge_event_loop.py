"""Conversion and indexing leave the API's event loop alone (S39-26, ADR 0051).

On the dev tenant (0.18.1) fourteen uploads into an `index` base pinned the single-CPU API pod and
the ingress answered 502 to unrelated requests for minutes: the API stopped answering while it cut
and indexed. A slow, CPU-bound step must cost the other requests nothing.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient

BASE = "/api/v1/orgs/varga/knowledge"
#: How long the deliberately CPU-bound step spins, and what a concurrent request may wait.
SPIN_S = 1.5
BUDGET_S = 0.5


def _mark() -> None:
    """The worker process says it ran the heavy step: a file the test reads."""
    with open(os.environ["HEAVY_MARKER"], "a") as handle:
        handle.write(str(os.getpid()))


def heavy_chunk(markdown: str, title: str, **kw: Any) -> Any:
    from choregos_api.knowledge.chunking import chunk

    _mark()
    _spin(SPIN_S)
    return chunk(markdown, title, **kw)


def heavy_rankings(query: str, vector: list[float], rows: Any) -> Any:
    from choregos_api.knowledge.index import _rankings

    _mark()
    _spin(SPIN_S)
    return _rankings(query, vector, rows)


@pytest.fixture(autouse=True)
async def _worker(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    from choregos_api.knowledge import offload

    monkeypatch.setenv("HEAVY_MARKER", str(tmp_path / "heavy"))
    # The spawned worker imports this module by name and inherits the parent's `sys.path`.
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[3]))
    offload.shutdown()
    yield
    offload.shutdown()


async def _answers_meanwhile(client: AsyncClient, work: Any) -> float:
    """The longest a concurrent GET (or a loop tick) waited while `work()` ran."""
    worst = 0.0
    done = asyncio.Event()

    async def probe() -> None:
        nonlocal worst
        while not done.is_set():
            started = time.monotonic()
            answered = await client.get(f"{BASE}/heavy")
            assert answered.status_code == 200
            worst = max(worst, time.monotonic() - started)
            await asyncio.sleep(0.02)

    async def ticker() -> None:
        nonlocal worst
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            worst = max(worst, now - last - 0.02)
            last = now

    async def job() -> None:
        try:
            await work()
        finally:
            done.set()

    await asyncio.gather(job(), probe(), ticker())
    return worst


def _spin(seconds: float) -> None:
    """Pure-Python CPU work (holds the GIL between its periodic releases), not a sleep."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        sum(i * i for i in range(2000))


async def _base(client: AsyncClient) -> None:
    created = await client.post(BASE, json={"slug": "heavy", "name": "Heavy", "serving_mode": "index"})
    assert created.status_code == 201, created.text


async def test_a_cpu_heavy_indexing_does_not_starve_other_requests(
    client: AsyncClient, admin: str, monkeypatch: Any, tmp_path: Any
) -> None:
    from choregos_api.knowledge import index
    from choregos_api.knowledge.conversion import conversions

    monkeypatch.setattr(index, "chunk", heavy_chunk)
    await _base(client)
    sent = await client.post(
        f"{BASE}/heavy/documents", files={"file": ("a.md", b"# Title\n\nSome text about tls.\n")}
    )
    assert sent.status_code == 202, sent.text
    worst = await _answers_meanwhile(client, conversions.drain)
    assert (tmp_path / "heavy").exists(), "the heavy step ran"
    assert worst < BUDGET_S, f"a request waited {worst:.2f} s while the index was being built"


async def test_a_cpu_heavy_search_does_not_starve_other_requests(
    client: AsyncClient, admin: str, monkeypatch: Any, tmp_path: Any
) -> None:
    from choregos_api.knowledge import index
    from choregos_api.knowledge.conversion import conversions

    await _base(client)
    await client.post(f"{BASE}/heavy/documents", files={"file": ("a.md", b"# Title\n\ntls text\n")})
    await conversions.drain()
    monkeypatch.setattr(index, "_rankings", heavy_rankings)

    async def search() -> None:
        found = await client.post(f"{BASE}/heavy/search", json={"query": "tls"})
        assert found.status_code == 200, found.text

    worst = await _answers_meanwhile(client, search)
    if index.get_settings().is_sqlite:  # the in-process ranking is the SQLite one
        assert (tmp_path / "heavy").exists(), "the heavy step ran"
        assert worst < BUDGET_S, f"a request waited {worst:.2f} s during a search"


async def test_the_converter_runs_at_a_low_priority() -> None:
    from choregos_api.knowledge.conversion import CONVERTER_NICE, _lower_priority

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import os; print(os.nice(0))",
        stdout=asyncio.subprocess.PIPE,
        preexec_fn=_lower_priority,
    )
    out, _ = await process.communicate()
    assert int(out) >= os.nice(0) + CONVERTER_NICE or int(out) == 19
