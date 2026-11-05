"""A run's tool call holds no pooled connection while its external call runs (#1354).

The route used to make every external call inside the request's transaction, open from the run-token
check to the commit at exit: on PostgreSQL each call in flight held one pooled connection `idle in
transaction` for as long as the connector, the page or the search took, and a few scripted runs
(ADR 0122 §7) filled a replica's pool. Each test below looks at the engine's pool at the moment the
external call runs — the fake provider is asked — and finds no connection checked out: the request's
transaction committed before the call left, and its line is booked after it.

These run on SQLite (the pool is a queue there too) and, in CI's second pass, on PostgreSQL under RLS
(`CHOREGOS_TEST_SUITE_ON_POSTGRES=1`), where the booking after the call also proves that the run
token's scope is set again. The pool itself, exhausted and not, is `test_tool_call_pool_postgres.py`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import structlog
from httpx import AsyncClient

from .kits.extension_tools import run_extension_tool
from .kits.tool_catalogue import run_with_catalogue
from .kits.tool_ledger import cap_the_project, tool_lines
from .test_broker import ORG, _prepare, supplier  # noqa: F401 - `supplier` is a fixture
from .test_web_fetch_tool import HTML, PAGE, web  # noqa: F401 - `web` is a fixture
from .test_web_fetch_tool import _ready as web_ready
from .test_web_search_tool import _ready as search_ready
from .test_web_search_tool import backend  # noqa: F401 - `backend` is a fixture

pytestmark = pytest.mark.asyncio

TOOL = "recherche_profils"
READ = "fournisseur__suivi_commande"
WRITE = "fournisseur__commander_poste"


def checked_out() -> int:
    """The connections the API's engine has lent out right now."""
    from choregos_api.db.session import get_engine

    return int(get_engine().pool.checkedout())


def _catalogue_provider(monkeypatch: pytest.MonkeyPatch, seen: list[int]) -> None:
    """The catalogue's provider, which notes the connections lent out when it is asked."""
    original = httpx.AsyncClient.request

    async def answer(client: Any, *args: Any, **kwargs: Any) -> httpx.Response:
        url = str(args[1]) if len(args) > 1 else str(kwargs.get("url", ""))
        if not url.startswith("https://api.annuaire.example"):
            return await original(client, *args, **kwargs)
        seen.append(checked_out())
        return httpx.Response(200, json={"profiles": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "request", answer)


# ───────────────────────────── each door the route calls out through ─────────────────────────────


@pytest.mark.parametrize("cap", [None, 5])
async def test_a_catalogue_call_runs_with_no_connection_held(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cap: int | None,
) -> None:
    """Without a cap the request's reads are still open when the call leaves; under one, its place is
    committed first and the run token's scope set again, which on PostgreSQL opens the next one."""
    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch, run_id="run-no-conn-cat")
    if cap is not None:
        await cap_the_project(project["id"], cap)
    seen: list[int] = []
    _catalogue_provider(monkeypatch, seen)

    answer = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/{TOOL}", headers=headers, json={"metier": "x"}
    )

    assert answer.status_code == 200 and answer.json()["status_code"] == 200, answer.text
    assert seen == [0], "the request held a connection while the provider answered"
    assert [line.provider for line in await tool_lines(run_id)] == ["annuaire"]
    assert answer.json()["remaining"] == (cap - 1 if cap else None)


async def test_a_broker_read_runs_with_no_connection_held(
    client: AsyncClient,
    project: dict[str, Any],
    supplier: Any,  # noqa: F811 - the fixture imported above
) -> None:
    base, headers = await _prepare(client, project, ["*"])
    seen: list[int] = []

    def track(arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append(checked_out())
        return {"numero": arguments["numero"], "state": "shipped"}

    supplier.gestures["suivi_commande"] = track

    answer = await client.post(f"{base}/{READ}", headers=headers, json={"numero": "42"})

    assert answer.status_code == 200 and answer.json()["status_code"] == 200, answer.text
    assert seen == [0], "the request held a connection while the connector answered"
    assert [line.provider for line in await tool_lines("run-courtier-1")] == ["mcp:fournisseur"]


async def test_a_governed_write_waits_for_its_outcome_with_no_connection_held(
    client: AsyncClient,
    project: dict[str, Any],
    supplier: Any,  # noqa: F811 - the fixture imported above
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The broker commits the approved action, starts its workflow and polls its status (#241): the
    request holds nothing while Temporal is asked, nor between two polls, each a short read of its own."""
    from choregos_api.services import broker
    from choregos_api.temporal import get_temporal

    base, headers = await _prepare(client, project, ["*"])
    opened = await client.patch(
        f"{ORG}/connectors/fournisseur/operations/commander_poste", json={"policy": "allowed"}
    )
    assert opened.status_code == 200, opened.text
    at_start: list[int] = []
    between_polls: list[int] = []
    temporal = get_temporal()
    start = temporal.start_action

    async def starting(workflow_id: str, payload: dict[str, Any]) -> str:
        at_start.append(checked_out())
        return await start(workflow_id, payload)

    async def pausing(delay: float) -> None:
        between_polls.append(checked_out())
        await asyncio.sleep(delay)

    monkeypatch.setattr(temporal, "start_action", starting)
    monkeypatch.setattr(broker, "asyncio", SimpleNamespace(sleep=pausing))

    # The first call creates the action; the second, the same, finds it and waits for it again.
    for _ in range(2):
        answer = await client.post(f"{base}/{WRITE}", headers=headers, json={"modele": "x14"})
        assert answer.status_code == 200 and answer.json()["status_code"] == 202, answer.text

    assert at_start == [0], "the request held a connection while the workflow was started"
    assert between_polls and set(between_polls) == {0}, between_polls
    assert [line.provider for line in await tool_lines("run-courtier-1")] == ["mcp:fournisseur"] * 2


async def test_a_web_fetch_resolves_and_reads_with_no_connection_held(
    client: AsyncClient,
    project: dict[str, Any],
    web: Any,  # noqa: F811 - the fixture imported above
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three external calls in one fetch: the host's address, its robots.txt, the page."""
    from choregos_api.services import web_fetch

    web.add(PAGE, HTML)
    base, headers = await web_ready(client, project)
    seen: list[tuple[str, int]] = []
    request, resolver = web.request, web_fetch.get_resolver()

    async def reading(method: str, url: str, **kwargs: Any) -> Any:
        seen.append((url, checked_out()))
        return await request(method, url, **kwargs)

    async def resolving(host: str) -> list[str]:
        seen.append((host, checked_out()))
        return await resolver(host)

    monkeypatch.setattr(web, "request", reading)
    web_fetch.set_resolver(resolving)

    answer = await client.post(f"{base}/web_fetch", headers=headers, json={"url": PAGE})

    assert answer.status_code == 200 and answer.json()["status_code"] == 200, answer.text
    assert seen == [
        ("docs.example.com", 0),
        ("https://docs.example.com/robots.txt", 0),
        (PAGE, 0),
    ], "the request held a connection during an external call"


async def test_a_web_search_runs_with_no_connection_held(
    client: AsyncClient,
    project: dict[str, Any],
    backend: Any,  # noqa: F811 - the fixture imported above
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, headers = await search_ready(client, project, ceiling={"mode": "allow"})
    seen: list[int] = []
    search = backend.search

    async def searching(query: str, **filters: Any) -> list[dict[str, Any]]:
        seen.append(checked_out())
        return list(await search(query, **filters))

    monkeypatch.setattr(backend, "search", searching)

    answer = await client.post(f"{base}/web_search", headers=headers, json={"query": "asyncio"})

    assert answer.status_code == 200 and answer.json()["status_code"] == 200, answer.text
    assert seen == [0], "the request held a connection while the backend searched"


async def test_an_extension_is_called_with_no_connection_held_and_may_still_read(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The extension gets the session with no transaction open; what it reads begins one, under the run
    token's scope, and is booked with the call's line."""
    from choregos_api import extensions
    from choregos_api.db.models import Run
    from sqlalchemy import select

    tool = {"name": "lookup", "description": "Looks up.", "inputSchema": {"type": "object"}}
    seen: list[tuple[bool, int]] = []

    async def list_tools(session: Any, run: Any, project: Any) -> list[dict[str, Any]]:
        return [dict(tool)]

    async def call_tool(session: Any, run: Any, project: Any, name: str, arguments: Any) -> tuple[int, Any]:
        seen.append((session.in_transaction(), checked_out()))
        # A statement, not the identity map: it begins a transaction, which RLS reads on PostgreSQL.
        again = (await session.execute(select(Run.id).where(Run.id == run.id))).scalar_one_or_none()
        return 200, {"read": again == run.id}

    monkeypatch.setitem(
        extensions._TOOL_PROVIDERS, "no_connection", extensions.ToolProvider(list_tools, call_tool)
    )
    run_id, headers = await run_extension_tool(project["id"])

    answer = await client.post(f"/api/v1/internal/runs/{run_id}/tools/lookup", headers=headers, json={})

    assert answer.status_code == 200, answer.text
    assert answer.json()["result"] == {"read": True}, "the run is visible to the extension's own read"
    assert seen == [(False, 0)], "the extension was called inside the request's transaction"
    assert [line.provider for line in await tool_lines(run_id)] == ["greffon:no_connection"]


# ─────────────────────────── what the booking after the call does when it fails ───────────────────────────


async def test_a_booking_that_fails_after_the_call_is_said_and_keeps_the_place(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The call left and answered; its line, its event and its place given back are one transaction
    after it. When that transaction fails the caller gets a 500, the log names what was not booked, and
    the place taken before the call stays: the cap still counts the call (S57-03)."""
    from choregos_api.routers import internal
    from choregos_core.tool_ledger import IN_FLIGHT

    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch, run_id="run-no-conn-fail")
    await cap_the_project(project["id"], 5)
    seen: list[int] = []
    _catalogue_provider(monkeypatch, seen)

    async def lost(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("the database went away after the call")

    monkeypatch.setattr(internal, "persist_event", lost)

    with structlog.testing.capture_logs() as logged:
        answer = await client.post(
            f"/api/v1/internal/runs/{run_id}/tools/{TOOL}", headers=headers, json={"metier": "x"}
        )

    assert answer.status_code == 500, answer.text
    assert seen == [0], "the call itself was made"
    (not_booked,) = [entry for entry in logged if entry["event"] == "tool_call.not_booked"]
    assert not_booked["log_level"] == "warning"
    assert (not_booked["run_id"], not_booked["tool"], not_booked["provider"]) == (run_id, TOOL, "annuaire")
    assert (not_booked["status_code"], not_booked["error"]) == (200, "ConnectionError")
    assert [line.provider for line in await tool_lines(run_id)] == [IN_FLIGHT]


# ───────────────────────────── the session's own guarantees ─────────────────────────────


async def test_a_session_nobody_allowed_keeps_its_transaction_across_the_block(app: Any) -> None:
    """A caller of the broker that did not opt in (every route but the tool route and the agent
    sessions', #1376) keeps its transaction: unchanged."""
    from choregos_api.db.session import outside_the_transaction, session_scope
    from sqlalchemy import text

    async with session_scope() as session:
        await session.execute(text("SELECT 1"))
        async with outside_the_transaction(session):
            assert session.in_transaction() and checked_out() == 1


async def test_an_allowed_session_commits_on_entry_runs_what_the_commit_released_and_reads_after(
    app: Any,
) -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import (
        after_commit,
        allow_release_during_calls,
        outside_the_transaction,
        session_scope,
    )
    from sqlalchemy import select

    started: list[bool] = []

    async def start() -> None:
        started.append(True)

    async with session_scope() as session:
        allow_release_during_calls(session)
        session.add(Organization(slug="released", name="Released"))
        await session.flush()
        after_commit(session, start)
        async with outside_the_transaction(session):
            assert not session.in_transaction() and checked_out() == 0
            # What the commit released leaves with it, as after any commit of a request (ADR 0085).
            assert started == [True]
        async with session_scope() as other:
            assert (await other.execute(select(Organization.slug))).scalars().all() == ["released"]
        assert (await session.execute(select(Organization.slug))).scalars().all() == ["released"]
    assert started == [True], "run once"
