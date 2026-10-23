"""Two real API processes on one PostgreSQL (ADR 0078 §4, ADR 0085, S40-23j).

S40-19c and S40-23f proved the replicas as two sessions or two clients in ONE process. Here each
replica is its own `uvicorn` process, started as the chart starts the API, on the CI's PostgreSQL
under RLS: the approved-actions relay of both processes, against a real Temporal, runs an action
once; an A2A stream served by one process wakes up when another process persists the work item's
event (`NOTIFY` across processes); and a `LISTEN` connection the server kills is taken back at once,
so the stream still wakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import pathlib
import socket
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from .conftest import PG_URL, SUITE_SUR_POSTGRES, _administrer
from .test_a2a_inbound import _ready, _send, _stream
from .test_a2a_replicas_and_limits import _persisted_elsewhere
from .test_action_relay import TASK_QUEUE, _state, _stranded, noop_effect, temporal_server, workers

__all__ = ["noop_effect", "temporal_server", "workers"]  # fixtures, used by name

pytestmark = pytest.mark.skipif(
    not SUITE_SUR_POSTGRES,
    reason="two processes share a database: PostgreSQL only (CHOREGOS_TEST_SUITE_SUR_POSTGRES)",
)


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ApiProcess:
    """One API replica: `uvicorn choregos_api.main:app`, its JSON log in a file."""

    def __init__(self, name: str, log: pathlib.Path, env: dict[str, str]) -> None:
        self.name, self.log = name, log
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        command = [sys.executable, "-m", "uvicorn", "choregos_api.main:app", "--host", "127.0.0.1"]
        self._out = log.open("w")
        self.proc = subprocess.Popen(
            [*command, "--port", str(self.port)], env=env, stdout=self._out, stderr=subprocess.STDOUT
        )

    async def ready(self) -> None:
        async with httpx.AsyncClient(base_url=self.url) as http:
            for _ in range(300):
                assert self.proc.poll() is None, f"{self.name} exited:\n{self.log.read_text()[-3000:]}"
                with contextlib.suppress(httpx.HTTPError):
                    if (await http.get("/healthz")).status_code == 200:
                        return
                await asyncio.sleep(0.2)
        raise AssertionError(f"{self.name} never answered /healthz:\n{self.log.read_text()[-3000:]}")

    def events(self, name: str) -> list[dict[str, Any]]:
        found = []
        for line in self.log.read_text().splitlines():
            with contextlib.suppress(ValueError):
                record = json.loads(line)
                if isinstance(record, dict) and record.get("event") == name:
                    found.append(record)
        return found

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self._out.close()


@pytest.fixture
async def replicas(app: Any, tmp_path: pathlib.Path) -> AsyncIterator[Callable[..., Any]]:
    """Starts replicas on the database `app` migrated; every one is stopped at the end."""
    started: list[ApiProcess] = []

    async def start(count: int, **env: str) -> list[ApiProcess]:
        base = {**os.environ, "CHOREGOS_LOG_JSON": "true", "CHOREGOS_LOG_LEVEL": "INFO", **env}
        names = [f"replica-{len(started) + i}" for i in range(count)]
        batch = [ApiProcess(name, tmp_path / f"{name}.log", base) for name in names]
        started.extend(batch)
        await asyncio.gather(*(r.ready() for r in batch))
        return batch

    try:
        yield start
    finally:
        for replica in started:
            replica.stop()


# ───────────────────────────── the approved-actions relay ─────────────────────────────


@pytest.mark.slow
async def test_two_api_processes_relaying_one_action_run_it_once(
    client: AsyncClient,
    project: dict[str, Any],
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
    temporal_server: Any,
    workers: Any,
    noop_effect: list[str],
    replicas: Callable[..., Any],
) -> None:
    """Both processes relay the stranded approval, pass after pass, before any worker listens:
    every start lands on the one `action-<id>`. Once the workers are there, its effect runs once."""
    stranded, pending = await _stranded(client, app, project["id"], monkeypatch)
    address = temporal_server.client.service_client.config.target_host
    pair = await replicas(
        2,
        CHOREGOS_FAKES="0",  # the real Temporal client, in both
        CHOREGOS_TEMPORAL_ADDRESS=address,
        CHOREGOS_TEMPORAL_NAMESPACE=temporal_server.client.namespace,
        CHOREGOS_TEMPORAL_TASK_QUEUE=TASK_QUEUE,
        CHOREGOS_ACTION_RELAY_INTERVAL_S="0.3",
    )
    for _ in range(200):
        if all(len(r.events("action.relay.started")) >= 3 for r in pair):
            break
        await asyncio.sleep(0.1)
    for replica in pair:
        passes = replica.events("action.relay.started")
        assert len(passes) >= 3, f"{replica.name} never relayed:\n{replica.log.read_text()[-3000:]}"
        assert all(p["actions"] == [stranded] for p in passes), passes
    handle = temporal_server.client.get_workflow_handle(f"action-{stranded}")
    run = (await handle.describe()).run_id
    async with workers:
        assert await asyncio.wait_for(handle.result(), timeout=60) == {"status": "succeeded", "effects": [0]}
        await asyncio.sleep(1.0)  # both processes pass again over a `succeeded` action
    assert (await handle.describe()).run_id == run, "every start from two processes lands on one run"
    assert noop_effect == [stranded], "two processes, one effect"
    assert await _state(stranded) == ("succeeded", [("done", 1)])
    assert (await _state(pending))[0] == "pending_approval"


# ───────────────────────────── events across processes ─────────────────────────────


async def _follow(replica: Any, token: str, task_id: str, then: Callable[[], Any]) -> list[dict[str, Any]]:
    """The frames of a `tasks/resubscribe` served by `replica`, while `then` runs elsewhere.

    `_stream` gives up after 10 s, and the stream's own fallback read comes after 30: a stream that
    ends in time was woken by the event."""
    async with AsyncClient(base_url=replica.url, timeout=30) as http:
        followed = asyncio.create_task(_stream(http, token, "tasks/resubscribe", {"id": task_id}))
        await asyncio.sleep(1.5)  # the stream is open and listens to the project
        await then()
        return [f["result"] for f in await followed]


async def test_a_stream_wakes_up_on_an_event_another_process_persists(
    client: AsyncClient, project: dict[str, Any], replicas: Callable[..., Any]
) -> None:
    token = await _ready(client, project)
    task = await _send(client, token)
    (served,) = await replicas(1)
    # The event is persisted by THIS process, as the orchestrator or another replica would.
    results = await _follow(served, token, task["id"], lambda: _persisted_elsewhere(task["id"]))
    assert [r["kind"] for r in results] == ["task", "status-update"]
    assert results[1]["status"]["state"] == "completed" and results[1]["final"] is True


async def _listeners() -> list[int]:
    """The backends of the replicas' `LISTEN` connections."""
    import asyncpg
    from choregos_api.events import APPLICATION_NAME

    conn = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        rows = await conn.fetch(
            "SELECT pid FROM pg_stat_activity WHERE application_name = $1", APPLICATION_NAME
        )
    finally:
        await conn.close()
    return [int(r["pid"]) for r in rows]


async def test_a_listen_connection_the_server_kills_is_taken_back_at_once(
    client: AsyncClient, project: dict[str, Any], replicas: Callable[..., Any]
) -> None:
    token = await _ready(client, project)
    task = await _send(client, token)
    (served,) = await replicas(1)
    for _ in range(100):
        if await _listeners():
            break
        await asyncio.sleep(0.1)
    (before,) = await _listeners()
    await _administrer(PG_URL, f"SELECT pg_terminate_backend({before})")
    back: list[int] = []
    for _ in range(50):  # five seconds, where the keepalive alone would take thirty
        back = [pid for pid in await _listeners() if pid != before]
        if back:
            break
        await asyncio.sleep(0.1)
    tail = served.log.read_text()[-2000:]
    assert back, f"the replica stayed deaf after its LISTEN connection was killed:\n{tail}"
    results = await _follow(served, token, task["id"], lambda: _persisted_elsewhere(task["id"]))
    assert [r["kind"] for r in results] == ["task", "status-update"], "the stream still wakes"
    assert results[1]["final"] is True
