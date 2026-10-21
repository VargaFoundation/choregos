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
#: How long the deliberately CPU-bound step spins.
SPIN_S = 1.5
#: Light requests that must be answered before a heavy step ends (an order, not a duration).
LIGHT_BEFORE_END = 3


def _mark() -> None:
    """The worker process says it ran the heavy step: a file the test reads."""
    with open(os.environ["HEAVY_MARKER"], "a") as handle:
        handle.write(str(os.getpid()))


def _mark_done() -> None:
    """The heavy step says its CPU-bound part is over: a second file the test reads."""
    with open(os.environ["HEAVY_DONE"], "a") as handle:
        handle.write(str(os.getpid()))


def heavy_chunk(markdown: str, title: str, **kw: Any) -> Any:
    from choregos_api.knowledge.chunking import chunk

    _mark()
    _spin(SPIN_S)
    _mark_done()
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
    monkeypatch.setenv("HEAVY_DONE", str(tmp_path / "heavy-done"))
    # The spawned worker imports this module by name and inherits the parent's `sys.path`.
    monkeypatch.syspath_prepend(str(pathlib.Path(__file__).resolve().parents[3]))
    offload.shutdown()
    yield
    offload.shutdown()


def _noop() -> None:
    """Starts the worker process before anything is measured."""


async def _warm(client: AsyncClient) -> None:
    """Starts the worker process and answers the probed route once, BEFORE any heavy work is queued:
    what is measured is the steady state (a cold start is slow on a CI runner whatever the loop does).
    """
    from choregos_api.knowledge import offload

    await offload.run(_noop)
    assert (await client.get(f"{BASE}/heavy")).status_code == 200


async def _answered_before_it_ended(client: AsyncClient, work: Any) -> int:
    """How many light GETs were answered while `work()` was still running (an ordering, not a time).

    A loop that is free answers them in between the heavy step's turns, whatever the machine's load;
    a loop the heavy step blocks answers none until `work()` has ended. `work()` is started first
    and the light GETs are only sent once it has begun.
    """
    ended = asyncio.Event()

    async def job() -> None:
        try:
            await work()
        finally:
            ended.set()

    task = asyncio.create_task(job())
    await asyncio.sleep(0)  # `work()` starts: an inline heavy step would run to its end right here
    answered_first = 0
    while not ended.is_set():
        reply = await client.get(f"{BASE}/heavy")
        assert reply.status_code == 200
        if not ended.is_set():  # this answer came back before the heavy work ended
            answered_first += 1
        await asyncio.sleep(0.02)
    await task
    return answered_first


async def _answered_during_the_spin(client: AsyncClient, work: Any, tmp_path: Any) -> int:
    """How many light GETs were sent after the heavy step began and answered before its spin ended.

    The step writes a file when it starts and another when its CPU-bound part is over, so this is an
    ordering, not a time. Indexing goes on after the spin (embedding, inserts, commit) and those
    awaits would let a few GETs through even if the spin held the loop, so the end of `work()` is
    not the right mark here: the end of the spin is. A free loop answers many GETs during it; a loop
    held by the spin answers none until it is over.
    """
    started, spun = tmp_path / "heavy", tmp_path / "heavy-done"
    task = asyncio.create_task(work())
    answered = 0
    while not spun.exists() and not task.done():
        if not started.exists():  # the worker has not begun: nothing to measure yet
            await asyncio.sleep(0.005)
            continue
        reply = await client.get(f"{BASE}/heavy")
        assert reply.status_code == 200
        if not spun.exists():
            answered += 1
        await asyncio.sleep(0.02)
    await task
    return answered


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
    await _warm(client)
    sent = await client.post(
        f"{BASE}/heavy/documents", files={"file": ("a.md", b"# Title\n\nSome text about tls.\n")}
    )
    assert sent.status_code == 202, sent.text
    answered = await _answered_during_the_spin(client, conversions.drain, tmp_path)
    assert (tmp_path / "heavy-done").exists(), "the heavy step ran"
    assert answered >= LIGHT_BEFORE_END, (
        f"only {answered} light request(s) were answered while the index was being built: "
        "the indexing held the event loop"
    )


async def test_a_cpu_heavy_search_does_not_starve_other_requests(
    client: AsyncClient, admin: str, monkeypatch: Any, tmp_path: Any
) -> None:
    from choregos_api.knowledge import index
    from choregos_api.knowledge.conversion import conversions

    await _base(client)
    await client.post(f"{BASE}/heavy/documents", files={"file": ("a.md", b"# Title\n\ntls text\n")})
    await conversions.drain()
    monkeypatch.setattr(index, "_rankings", heavy_rankings)
    await _warm(client)

    async def search() -> None:
        found = await client.post(f"{BASE}/heavy/search", json={"query": "tls"})
        assert found.status_code == 200, found.text

    answered_first = await _answered_before_it_ended(client, search)
    if index.get_settings().is_sqlite:  # the in-process ranking is the SQLite one
        assert (tmp_path / "heavy").exists(), "the heavy step ran"
        assert answered_first >= LIGHT_BEFORE_END, (
            f"only {answered_first} light request(s) were answered before the search ended: "
            "the search held the event loop"
        )


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
