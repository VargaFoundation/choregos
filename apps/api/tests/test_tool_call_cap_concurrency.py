"""The tool-call cap holds when a run's calls arrive at once (S57-03, ADR 0122 §7).

The route counted the run's `tool` lines, called the tool, and booked its line at the end: calls of
one run made at once each read the same count, and all of them passed. The concurrent test holds the
provider until every call has either reached it or been refused, so without the per-run lock and the
place taken before the call, all eight reach it.

These tests run on SQLite (the in-process lock of `lock_budget_scopes`) and, in CI's second pass, on
PostgreSQL under RLS (`CHOREGOS_TEST_SUITE_ON_POSTGRES=1`: `pg_advisory_xact_lock`, the migrated
schema, the non-superuser role). The PostgreSQL statement itself is checked on every backend by
`test_on_postgresql_the_lock_is_asked_for_before_the_count`.
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from .kits.tool_catalogue import run_with_catalogue
from .test_broker import ORG, _prepare, supplier  # noqa: F401 - `supplier` is a fixture
from .test_web_fetch_tool import HTML, PAGE, _ready, web  # noqa: F401 - `web` is a fixture

pytestmark = pytest.mark.asyncio

CAP = 3
AT_ONCE = 8
TOOL = "recherche_profils"
WRITE = "fournisseur__commander_poste"


class HeldProvider:
    """The catalogue's provider, answering only once `release` is set: calls pile up in flight."""

    def __init__(self) -> None:
        self.arrived = 0
        self.release = asyncio.Event()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = httpx.AsyncClient.request

        async def answer(client: Any, *args: Any, **kwargs: Any) -> httpx.Response:
            url = str(args[1]) if len(args) > 1 else str(kwargs.get("url", ""))
            if not url.startswith("https://api.annuaire.example"):
                return await original(client, *args, **kwargs)
            self.arrived += 1
            await self.release.wait()
            return httpx.Response(200, json={"profiles": []}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx.AsyncClient, "request", answer)


async def _cap_the_project(project_id: str, cap: int) -> None:
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        policy = (
            (
                await session.execute(
                    select(PolicyDef)
                    .where(PolicyDef.project_id == project_id)
                    .order_by(PolicyDef.version.desc())
                )
            )
            .scalars()
            .first()
        )
        assert policy is not None
        document = dict(policy.json_doc)
        document["budgets"] = {**(document.get("budgets") or {}), "tool_calls_per_run": cap}
        policy.json_doc = document


async def _tool_lines(run_id: str) -> list[Any]:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        query = select(CostLedger).where(CostLedger.run_id == run_id, CostLedger.kind == "tool")
        return list((await session.execute(query)).scalars())


async def test_eight_calls_at_once_at_cap_minus_one_let_exactly_one_through(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.services import tool_call_cap
    from choregos_api.services.tool_call_cap import IN_FLIGHT

    # A run of its own: the in-process lock that stands in on SQLite is kept per run for the process.
    run_id, headers = await run_with_catalogue(project, tmp_path, monkeypatch, run_id="run-cap-at-once")
    await _cap_the_project(project["id"], CAP)
    provider = HeldProvider()
    provider.install(monkeypatch)
    url = f"/api/v1/internal/runs/{run_id}/tools/{TOOL}"

    provider.release.set()
    for _ in range(CAP - 1):
        assert (await client.post(url, headers=headers, json={"metier": "x"})).status_code == 200
    provider.release.clear()
    provider.arrived = 0
    # The window between the count and the commit of the place is a few statements wide; widened here,
    # a count taken outside the lock is seen by the next call's count, on either backend.
    widen = tool_call_cap._scope

    async def slow_scope(session: Any) -> str | None:
        await asyncio.sleep(0.05)
        return await widen(session)

    monkeypatch.setattr(tool_call_cap, "_scope", slow_scope)

    calls = [
        asyncio.create_task(client.post(url, headers=headers, json={"metier": "x"})) for _ in range(AT_ONCE)
    ]
    # Every call has either reached the provider (and waits there) or been answered: only then does the
    # provider answer. Without the fix, all eight have counted `cap - 1` by then.
    deadline = asyncio.get_running_loop().time() + 30
    while provider.arrived + sum(call.done() for call in calls) < AT_ONCE:
        assert asyncio.get_running_loop().time() < deadline, "calls neither reached the provider nor ended"
        await asyncio.sleep(0.01)
    provider.release.set()
    answers = await asyncio.gather(*calls)

    codes = sorted(answer.status_code for answer in answers)
    assert codes == [200] + [429] * (AT_ONCE - 1), [answer.text for answer in answers]
    for refused in (answer for answer in answers if answer.status_code == 429):
        assert refused.json()["title"] == "Tool call cap reached", refused.text
    assert provider.arrived == 1, "only one call reached the provider"
    lines = await _tool_lines(run_id)
    assert len(lines) == CAP, [(line.provider, line.model) for line in lines]
    assert {line.provider for line in lines} == {"annuaire"}, f"no `{IN_FLIGHT}` line is left behind"
    assert next(a for a in answers if a.status_code == 200).json()["remaining"] == 0


async def test_a_call_refused_for_its_arguments_gives_its_place_back(
    client: AsyncClient,
    project: dict[str, Any],
    web: Any,  # noqa: F811 - the fixture imported above
) -> None:
    """A refused call was never counted; holding a place must not change that."""
    web.add(PAGE, HTML)
    base, headers = await _ready(client, project, extra="budgets:\n  tool_calls_per_run: 1\n")
    malformed = await client.post(f"{base}/web_fetch", headers=headers, json={"method": "GET"})
    assert malformed.status_code == 400, malformed.text
    assert await _tool_lines("run-web-1") == []
    fetched = await client.post(f"{base}/web_fetch", headers=headers, json={"url": PAGE})
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["remaining"] == 0
    capped = await client.post(f"{base}/web_fetch", headers=headers, json={"url": PAGE})
    assert capped.status_code == 429, capped.text


async def test_a_call_the_network_policy_stopped_still_does_not_count(
    client: AsyncClient,
    project: dict[str, Any],
    web: Any,  # noqa: F811 - the fixture imported above
) -> None:
    """ADR 0070: a call that never reached the server is not booked; its place is not kept either."""
    web.add(PAGE, HTML)
    base, headers = await _ready(client, project, extra="budgets:\n  tool_calls_per_run: 1\n")
    stopped = await client.post(
        f"{base}/web_fetch", headers=headers, json={"url": "https://api.stripe.com/v1/charges"}
    )
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["result"]["type"] == "network_policy"
    assert await _tool_lines("run-web-1") == []
    fetched = await client.post(f"{base}/web_fetch", headers=headers, json={"url": PAGE})
    assert fetched.status_code == 200 and fetched.json()["remaining"] == 0, fetched.text
    lines = await _tool_lines("run-web-1")
    assert [line.provider for line in lines] == ["web:docs.example.com"]


async def test_a_governed_write_that_commits_on_its_way_is_counted_once(
    client: AsyncClient,
    project: dict[str, Any],
    supplier: Any,  # noqa: F811 - the fixture imported above
) -> None:
    """The broker commits an allowed write before it waits for its workflow (#241): the place taken
    before it, and the line booked after it, are each kept once, under the run token's scope."""
    base, headers = await _prepare(client, project, ["*"])
    opened = await client.patch(
        f"{ORG}/connectors/fournisseur/operations/commander_poste", json={"policy": "allowed"}
    )
    assert opened.status_code == 200, opened.text
    await _cap_the_project(project["id"], 2)
    for model, remaining in (("x14", 1), ("x15", 0)):
        answer = await client.post(f"{base}/{WRITE}", headers=headers, json={"modele": model})
        assert answer.status_code == 200, answer.text
        assert (answer.json()["status_code"], answer.json()["remaining"]) == (202, remaining), answer.text
    capped = await client.post(f"{base}/{WRITE}", headers=headers, json={"modele": "x16"})
    assert capped.status_code == 429, capped.text
    lines = await _tool_lines("run-courtier-1")
    assert [line.provider for line in lines] == ["mcp:fournisseur", "mcp:fournisseur"]


class _Recorder:
    """A session that records what it is asked, and answers `count` to the count."""

    def __init__(self, count: int) -> None:
        self.count = count
        self.statements: list[tuple[str, Any]] = []
        self.added: list[Any] = []

    async def execute(self, statement: Any, parameters: Any = None) -> Any:
        sql = str(statement)
        self.statements.append((sql, parameters))
        return SimpleNamespace(scalar_one=lambda: "*" if "current_setting" in sql else self.count)

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.statements.append(("COMMIT", None))


@pytest.mark.parametrize("count", [CAP - 1, CAP])
async def test_on_postgresql_the_lock_is_asked_for_before_the_count(
    monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    """The SQLite suite cannot take a PostgreSQL lock; it checks the route asks for one, on the run,
    before it counts, and commits the place it takes before the call leaves."""
    from choregos_api.services import budgets, tool_call_cap

    postgres = SimpleNamespace(is_sqlite=False)
    monkeypatch.setattr(budgets, "get_settings", lambda: postgres)
    monkeypatch.setattr(tool_call_cap, "get_settings", lambda: postgres)
    session = _Recorder(count)
    run = SimpleNamespace(id="run-pg", project_id="p", work_item_id="w", stage_role="plan")

    slot = await tool_call_cap.take_tool_call_slot(session, run, TOOL, CAP)  # type: ignore[arg-type]

    lock, parameters = session.statements[0]
    assert lock == "SELECT pg_advisory_xact_lock(:n)"
    expected = int.from_bytes(hashlib.sha256(b"tool_calls:run-pg").digest()[:8], "big", signed=True)
    assert parameters == {"n": expected}
    assert "count(" in session.statements[1][0] and "cost_ledger" in session.statements[1][0]
    assert slot.already_called == count
    if count >= CAP:
        assert session.added == [] and len(session.statements) == 2, "the cap is reached: nothing is taken"
        return
    (line,) = session.added
    assert (line.kind, line.provider, line.model, line.run_id) == (
        "tool",
        tool_call_cap.IN_FLIGHT,
        TOOL,
        "run-pg",
    )
    after = [sql for sql, _ in session.statements[2:]]
    assert after[1] == "COMMIT" and "set_config('app.current_orgs'" in after[2], after


async def test_without_a_cap_nothing_is_locked_or_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.services import tool_call_cap

    session = _Recorder(0)
    run = SimpleNamespace(id="run-free", project_id="p", work_item_id="w", stage_role="plan")
    slot = await tool_call_cap.take_tool_call_slot(session, run, TOOL, 0)  # type: ignore[arg-type]
    assert session.statements == [] and slot.line_id is None
