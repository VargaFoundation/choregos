"""The outposts' door served alone: `python -m choregos_api.outposts` (S65-16, ADR 0148 §8).

Where a deployment splits the door out, its Deployment runs the API image with this entrypoint: the
four verbs, two probes, the wake-up channel of the relay's connection, and nothing else. These tests
hold it to that: what it serves and what it does not, what it imports, where the sweep runs (the
API, in both layouts, and never here), and that a process told to stop lets the polls it holds end.

They run on SQLite and, with `CHOREGOS_TEST_SUITE_ON_POSTGRES`, again on PostgreSQL. What only
PostgreSQL can show, a poll woken through this process's own `LISTEN` connection, is at the end.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from typing import Any

import choregos_contracts as contracts
import pytest
from choregos_contracts import outpost
from httpx import ASGITransport, AsyncClient

from .conftest import PG_URL, login, without_postgres
from .kits import outpost_daemon as daemon_kit
from .kits import outposts as kit
from .kits.outpost_daemon import POLL, Daemon, answer_path, refuse_path, refused

UNKNOWN_CALL = "8f0e6b1c-5a3d-4c57-9e21-7b2a4d9c0f13"
VERBS = {
    ("POST", "/outposts/v1/enrol"),
    ("POST", "/outposts/v1/poll"),
    ("POST", "/outposts/v1/calls/{id}/answer"),
    ("POST", "/outposts/v1/calls/{id}/refuse"),
}
PROBES = {("GET", "/healthz"), ("GET", "/readyz")}


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    """Outposts on, the door given its origin and its key, the deployment its key wrapper."""
    daemon_kit.key_wrapper(monkeypatch)
    return kit.switch_on(monkeypatch)


def _alone() -> Any:
    from choregos_api.outposts.alone import create_app

    return create_app()


@pytest.fixture
async def alone(app: Any) -> AsyncIterator[AsyncClient]:
    """The door served alone, over the database of the API that manages its outposts (`app`)."""
    async with AsyncClient(transport=ASGITransport(app=_alone()), base_url="http://door") as http:
        yield http


def _routes(application: Any) -> set[tuple[str, str]]:
    from fastapi.routing import iter_route_contexts

    return {
        (method, context.path or "")
        for context in iter_route_contexts(application.router.routes)
        for method in (context.methods or ())
        if method != "HEAD"
    }


# ───────────────────────── what it serves, and what it does not ─────────────────────────


def test_it_serves_the_four_verbs_its_two_probes_and_no_other_route() -> None:
    assert _routes(_alone()) == VERBS | PROBES
    # The four verbs are the contract's, read from its own document.
    declared = {
        (method.upper(), path)
        for path, verbs in contracts.load_outpost_door()["paths"].items()
        for method in verbs
        if method in {"get", "post", "put", "patch", "delete"}
    }
    assert declared == VERBS


async def test_no_route_of_the_api_answers_there(alone: AsyncClient) -> None:
    """Neither the REST API, nor the two other doors, nor what the API serves at its root."""
    for path in (
        "/api/v1/edition",
        f"/api/v1/orgs/{kit.ORG}/outposts",
        "/api/v1/auth/login",
        "/api/v1/internal/runs/x/tools",
        "/mcp",
        f"/a2a/orgs/{kit.ORG}/.well-known/agent-card.json",
        "/edition",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/downloads/manifest.json",
    ):
        assert (await alone.get(path)).status_code == 404, path
    assert (await alone.post("/mcp", content=b"{}")).status_code == 404
    assert (await alone.post(f"/api/v1{POLL}", content=b"{}")).status_code == 404


async def test_the_door_it_serves_is_the_contracts(alone: AsyncClient) -> None:
    """The same door as the API's: an unsigned request meets the contract's problem, with a nonce."""
    for path in (POLL, answer_path(UNKNOWN_CALL), refuse_path(UNKNOWN_CALL)):
        response = await alone.post(path, content=b"{}")
        refused(response, "invalid_signature", 401)
        assert response.headers[outpost.NONCE_HEADER]
        assert response.headers["x-request-id"]


async def test_an_instance_enrols_polls_and_answers_through_it(
    client: AsyncClient, admin: str, alone: AsyncClient
) -> None:
    """A person manages the outpost at the API; its daemon only ever meets the door served alone."""
    await kit.create(client, daemon_kit.SLUG)
    daemon = Daemon()
    enrolled = await daemon.enrol(alone, await kit.token_for(client, daemon_kit.SLUG))
    assert enrolled.status_code == 201, enrolled.text
    refused(await daemon.poll(alone), "instance_pending", 403)
    await kit.approve(client, daemon_kit.SLUG, str(daemon.instance_id))
    placed = await daemon_kit.place()
    (call,) = await daemon.calls(alone)
    assert call["id"] == placed.id
    answered = await daemon.answer(alone, call)
    assert answered.status_code == 200 and answered.json() == {"id": call["id"], "recorded": "answer"}


async def test_an_error_is_answered_there_as_the_api_answers_it(
    alone: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same door in both layouts, down to what goes wrong: an error of the API keeps its status,
    and an unexpected one is a problem that carries the id of its log line, never a stack trace."""
    from choregos_api.errors import conflict
    from choregos_api.outposts import auth

    async def refuses(*_: Any) -> bytes:
        raise conflict("a row another replica holds")

    monkeypatch.setattr(auth, "read_body", refuses)
    held = await alone.post(POLL, content=b"{}")
    assert held.status_code == 409 and held.headers["content-type"].startswith("application/problem+json")

    async def breaks(*_: Any) -> bytes:
        raise RuntimeError("a secret that must not leave: hunter2")

    monkeypatch.setattr(auth, "read_body", breaks)
    broken = await alone.post(POLL, content=b"{}", headers={"x-request-id": "from-the-entrance-42"})
    assert broken.status_code == 500 and broken.headers["content-type"].startswith("application/problem+json")
    assert broken.json()["correlation_id"] == broken.headers["x-request-id"] == "from-the-entrance-42"
    assert "hunter2" not in broken.text and "Traceback" not in broken.text


# ───────────────────────── built only where there is a door to serve ─────────────────────────


def test_it_is_not_built_while_outposts_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    kit.configure(monkeypatch, outposts="off")
    with pytest.raises(RuntimeError, match="CHOREGOS_OUTPOSTS is off"):
        _alone()


@pytest.mark.parametrize("missing", ["outposts_door_url", "outposts_door_signing_key"])
def test_it_is_not_built_without_the_doors_origin_or_its_key(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    kit.configure(monkeypatch, **{missing: None})
    with pytest.raises(RuntimeError, match=f"CHOREGOS_{missing.upper()}"):
        _alone()


def test_an_extension_that_does_not_load_stops_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """An edition's key wrapper is an extension (ADR 0103): the door served alone loads them as the
    API does, and a declared one that fails stops the start-up rather than hand nothing over."""
    from choregos_api.outposts import alone as module

    def fails() -> list[str]:
        raise RuntimeError('extension "house": cannot load')

    monkeypatch.setattr(module, "load_extensions", fails)
    with pytest.raises(RuntimeError, match="cannot load"):
        module.create_app()


# ───────────────────────── what it imports ─────────────────────────

_CLOSURE = """
import json, sys
from choregos_api.outposts.alone import create_app

create_app()
print(json.dumps(sorted(name for name in sys.modules if name.startswith("choregos_"))))
"""
#: What the process that serves the door alone never loads: the application of the API, its routers,
#: the two other doors, and what reaches an organisation's connectors.
NEVER_IMPORTED = (
    "choregos_api.main",
    "choregos_api.routers",
    "choregos_api.mcp",
    "choregos_api.a2a",
    "choregos_api.metrics",
    "choregos_api.services.broker",
    "choregos_api.services.mcp_gateway",
    "choregos_adapters.mcp",
    "choregos_orchestrator",
)


def test_serving_the_door_alone_loads_no_other_part_of_the_api(door: kit.Door) -> None:
    """In a process of its own, as the Deployment starts it: building the application imports the
    door's package and what it stands on, and none of the API's routers."""
    done = subprocess.run(
        [sys.executable, "-c", _CLOSURE],
        env={**os.environ, "CHOREGOS_FAKES": "1"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    loaded = json.loads(done.stdout.strip().splitlines()[-1])
    assert "choregos_api.outposts.door" in loaded, "the probe no longer builds the door"
    reached = [
        name
        for name in loaded
        if any(name == never or name.startswith(f"{never}.") for never in NEVER_IMPORTED)
    ]
    assert not reached, f"serving the door alone loads {reached}"


# ───────────────────────── the probes ─────────────────────────


async def test_it_is_alive_and_ready_when_the_database_answers(alone: AsyncClient) -> None:
    assert (await alone.get("/healthz")).json() == {"status": "ok"}
    ready = await alone.get("/readyz")
    assert ready.status_code == 200 and ready.json() == {"status": "ready"}


async def test_it_is_not_ready_while_the_database_does_not_answer(
    alone: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.outposts import alone as module

    def unreachable() -> Any:
        raise OSError("connection refused")

    monkeypatch.setattr(module, "get_engine", unreachable)
    not_ready = await alone.get("/readyz")
    assert not_ready.status_code == 503 and not_ready.json()["status"] == "not_ready"
    # Alive all the same: a database that blinks must not have the process restarted.
    assert (await alone.get("/healthz")).status_code == 200


# ───────────────────────── where the sweep runs ─────────────────────────


@pytest.fixture
def sweeps(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every start of the platform's sweep, in whichever process's start-up it happens."""
    from choregos_api.outposts import rendezvous

    started: list[str] = []

    async def recorded(interval: float | None = None) -> None:
        started.append("sweep")
        await asyncio.Event().wait()

    monkeypatch.setattr(rendezvous, "sweep_loop", recorded)
    return started


async def test_the_api_runs_the_sweep_and_the_door_served_alone_never_does(
    app: Any, sweeps: list[str]
) -> None:
    """Exactly one place closes what ran out of time at outposts nobody polls: the API, whether or
    not the door is split out. The sweep lists every outpost under the platform's scope, which the
    door never takes (ADR 0148 §6)."""
    from choregos_api import main
    from choregos_api.outposts import alone as module

    async with module.lifespan(_alone()):
        await asyncio.sleep(0.05)
    assert sweeps == [], "the door served alone started the platform's sweep"
    async with main.lifespan(app):
        await asyncio.sleep(0.05)
    assert sweeps == ["sweep"], "the API no longer runs the sweep while outposts are on"


# ───────────────────────── stopping ─────────────────────────


def test_the_entrypoint_gives_the_polls_in_hand_the_time_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn
    from choregos_api.outposts import alone as module
    from choregos_api.outposts.door import SLOT_MARGIN_S

    seen: dict[str, Any] = {}
    monkeypatch.setattr(
        uvicorn, "run", lambda application, **options: seen.update(app=application, **options)
    )
    module.main()
    assert seen["app"] == "choregos_api.outposts.alone:create_app" and seen["factory"] is True
    # Longer than the longest poll: past it what is left is cancelled, and gives its slot back.
    assert seen["timeout_graceful_shutdown"] == outpost.MAX_WAIT_S + SLOT_MARGIN_S == module.GRACE_S
    assert seen["port"] == 8000 and seen["log_config"] is None


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _until(condition: Any, what: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        with contextlib.suppress(Exception):
            if await condition():
                return
        assert time.monotonic() < deadline, f"{what}: it never happened"
        await asyncio.sleep(0.1)


async def test_a_process_told_to_stop_lets_the_poll_it_holds_end(
    client: AsyncClient, admin: str, tmp_path: Any
) -> None:
    """The real entrypoint, in a process of its own, over this test's database: `SIGTERM` while a poll
    waits. The poll answers as if nothing had happened, its slot is given back, and the process ends
    by itself, its shutdown run to the end."""
    daemon = await daemon_kit.approved(client)
    port = _free_port()
    log = tmp_path / "door.log"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "choregos_api.outposts"],
            env={**os.environ, "CHOREGOS_PORT": str(port), "CHOREGOS_LOG_LEVEL": "INFO"},
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    try:
        async with AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30) as http:

            async def ready() -> bool:
                return (await http.get("/readyz")).status_code == 200

            await _until(ready, "the door served alone never became ready")
            poll = asyncio.ensure_future(daemon.poll(http, wait_s=3))

            async def waits() -> bool:
                return (await daemon_kit.instance_row(str(daemon.instance_id))).poll_holder is not None

            await _until(waits, "the poll never took its slot")
            asked = time.monotonic()
            process.send_signal(signal.SIGTERM)
            answer = await poll
            assert answer.status_code == 200, answer.text
            assert answer.json()["calls"] == [] and answer.json()["stop"] is False
            assert time.monotonic() - asked > 1.0, "the poll was cut short instead of left to end"
        # The server hands the signal back to the process once it has shut down: ended by `SIGTERM`
        # after its own shutdown, never killed for overstaying.
        ended = await asyncio.to_thread(process.wait, 30)
        said = log.read_text(encoding="utf-8")
        assert ended in (0, -signal.SIGTERM), said[-2000:]
        assert "the outposts' door stops" in said, "the shutdown did not run to its end"
        assert (await daemon_kit.instance_row(str(daemon.instance_id))).poll_holder is None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


# ───────────────────────── PostgreSQL: its own LISTEN connection ─────────────────────────


@pytest.fixture
async def pg_client(pg_app: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", kit.ORG)
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
    reset_settings_cache()
    async with session_scope(orgs="*") as session:
        session.add(Organization(slug=kit.ORG, name="Varga Foundation"))
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as http:
        await login(http, "admin@varga.dev")
        yield http


@without_postgres
async def test_a_poll_is_woken_through_the_processs_own_listen_connection(
    pg_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under its own start-up and no other: a call written is handed over within a second, where the
    periodic look alone would take five to ten."""
    from choregos_api.outposts import alone as module
    from choregos_api.outposts import rendezvous

    listening: list[tuple[bool, list[str]]] = []

    class Recorded(module.PostgresRelay):  # type: ignore[misc]
        """The relay the start-up builds, and what it was asked to listen to."""

        def __init__(self, dsn: str, *, events: bool = True) -> None:
            super().__init__(dsn, events=events)
            listening.append((events, []))

        def listen_also(self, channel: str, heard: Any) -> None:
            listening[-1][1].append(channel)
            super().listen_also(channel, heard)

    monkeypatch.setattr(module, "PostgresRelay", Recorded)
    daemon = await daemon_kit.approved(pg_client)
    application = _alone()
    async with (
        module.lifespan(application),
        AsyncClient(transport=ASGITransport(app=application), base_url="http://door") as http,
    ):
        poll = asyncio.ensure_future(daemon.calls(http, wait_s=20))

        async def waits() -> bool:
            return rendezvous.waiters().waiting() == 1

        await _until(waits, "the poll never waited")
        # Give the relay the time to attach: a notification sent before it listens is not heard.
        heard = rendezvous.waiters().heard
        await _until_heard_or_attached(heard)
        placed_at = time.monotonic()
        placed = await daemon_kit.place()
        (call,) = await asyncio.wait_for(poll, timeout=4)
        assert call["id"] == placed.id
        assert time.monotonic() - placed_at < 2.0, "the poll waited for its periodic look"
    # One connection, on the wake-ups' channel, and not on the platform's events.
    assert listening == [(False, [rendezvous.CHANNEL])]


async def _until_heard_or_attached(heard_before: int) -> None:
    """Wait until this process's relay listens: a probe notification on the outposts' channel comes
    back to it."""
    import asyncpg
    from choregos_api.outposts import rendezvous

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:

        async def attached() -> bool:
            await connection.execute(f"SELECT pg_notify('{rendezvous.CHANNEL}', 'probe')")
            await asyncio.sleep(0.05)
            return rendezvous.waiters().heard > heard_before

        await _until(attached, "the relay of the door served alone never listened", timeout_s=20)
    finally:
        await connection.close()


@without_postgres
async def test_the_relay_of_the_door_served_alone_hears_no_platform_event(pg_app: Any) -> None:
    """`events=False`: the connection listens on the wake-up channel and not on the platform's events,
    which nobody in that process would be woken by. The same event reaches a relay that listens to
    them: it was sent, and it would have been counted."""
    import asyncpg
    from choregos_api.config import get_settings
    from choregos_api.events import CHANNEL, PostgresRelay
    from choregos_contracts import ChoregosEvent, EventType

    heard: list[str] = []
    wake_ups = "choregos_test_wake_ups"
    alone = PostgresRelay(get_settings().database_url, events=False)
    alone.listen_also(wake_ups, heard.append)
    whole = PostgresRelay(get_settings().database_url)
    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    alone.start()
    whole.start()
    try:
        await asyncio.wait_for(alone.attached.wait(), timeout=10)
        await asyncio.wait_for(whole.attached.wait(), timeout=10)
        event = ChoregosEvent.emit(next(iter(EventType)), subject="a subject").model_dump_json()
        await connection.execute("SELECT pg_notify($1, $2)", CHANNEL, event)
        await connection.execute("SELECT pg_notify($1, $2)", wake_ups, "an id")

        async def both_heard() -> bool:
            return heard == ["an id"] and whole.received == 1

        await _until(both_heard, "the notifications were not heard", timeout_s=10)
        assert alone.received == 0, "the relay of the door served alone heard a platform event"
    finally:
        await connection.close()
        await alone.stop()
        await whole.stop()


@without_postgres
@pytest.mark.parametrize(("env", "starts"), [("prod", False), ("staging", False), ("dev", True)])
async def test_it_does_not_serve_as_a_superuser_outside_development(
    pg_app: Any, monkeypatch: pytest.MonkeyPatch, env: str, starts: bool
) -> None:
    """The door keeps one outpost from another's rows by row-level security alone (ADR 0148 §6), and a
    PostgreSQL superuser ignores it: the same refusal as the API's start-up, in the process that
    serves the door alone."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.outposts import alone as module

    application = _alone()
    # The test container's own role is a superuser: the one a deployment must never hand the door.
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", PG_URL)
    monkeypatch.setenv("CHOREGOS_ENV", env)
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")  # the settings refuse it outside development
    reset_settings_cache()
    await db_session.dispose_engine()
    try:
        if starts:
            async with module.lifespan(application):
                pass
        else:
            with pytest.raises(RuntimeError, match="SUPERUSER"):
                async with module.lifespan(application):
                    pass
    finally:
        await db_session.dispose_engine()
        monkeypatch.undo()
        reset_settings_cache()
