"""Tool calls held open do not exhaust a replica's pool (#1354), on a real PostgreSQL.

Each call in flight used to hold one pooled connection `idle in transaction` for as long as its
provider took: with the tool bridge (ADR 0122 §7) four scripting runs filled a replica's pool, and
every other request of the replica waited `db_pool_timeout_s`, then failed. Here the pool is two
connections with no overflow and a two-second wait; three calls of three runs are held at their
provider. Without the fix the third call never reaches its provider and `/readyz` waits out the pool;
with it, the three wait at the provider holding nothing, `pg_stat_activity` shows no session of the
application role idle in transaction, and the calls are booked once they answer, under RLS.

Runs only against a real PostgreSQL (`CHOREGOS_TEST_DATABASE_URL`), as the application role (not a
superuser, so RLS applies); the CI starts one, locally `docs/development.md` says how.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from .conftest import APP_ROLE, PG_URL, without_postgres
from .kits.tool_catalogue import CATALOGUE

pytestmark = [without_postgres, pytest.mark.asyncio]

POOL_SIZE = 2
# Long enough that a loaded CI runner serves `/readyz` within it (2 s gave 2.19 s under xdist, #1603),
# short enough that the defect, which waits it out in full, still fails fast.
POOL_TIMEOUT_S = 5
HELD = 3
TOOL = "recherche_profils"


class HeldProvider:
    """The catalogue's provider, answering only once `release` is set."""

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


async def _small_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """The engine built again with a pool of `POOL_SIZE`, no overflow, a short wait."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session

    monkeypatch.setenv("CHOREGOS_DB_POOL_SIZE", str(POOL_SIZE))
    monkeypatch.setenv("CHOREGOS_DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("CHOREGOS_DB_POOL_TIMEOUT_S", str(POOL_TIMEOUT_S))
    reset_settings_cache()
    await db_session.dispose_engine()


async def _runs_with_catalogue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int) -> list[Any]:
    """An organisation, a project offered the catalogue tool, `count` running runs and their tokens."""
    from choregos_api import catalogue as catalogue_service
    from choregos_api.db.models import Organization, Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_api.services import ensure_defaults

    file = tmp_path / "catalogue.yaml"
    file.write_text(CATALOGUE, encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(file))
    monkeypatch.setenv("ANNUAIRE_API_KEY", "key-from-the-vault")
    catalogue_service.clear_cache()
    runs: list[tuple[str, dict[str, str]]] = []
    async with session_scope(orgs="*") as session:
        organisation = Organization(slug="a", name="A")
        session.add(organisation)
        await session.flush()
        project = Project(
            org_id=organisation.id,
            slug="billing-api",
            name="Billing",
            status="active",
            config={"slug": "billing-api", "org": "a", "tools": [TOOL]},
        )
        session.add(project)
        await session.flush()
        await ensure_defaults(session, project)
        for index in range(count):
            key = f"a-{index}"
            item = WorkItem(project_id=project.id, tracker_key=key, title="T", state="ready")
            session.add(item)
            await session.flush()
            run_id = f"run-pool-{index}"
            session.add(
                Run(
                    id=run_id,
                    work_item_id=item.id,
                    project_id=project.id,
                    stage_role="custom",
                    status="running",
                )
            )
            token = mint_run_token(run_id, project_slug="billing-api", work_item_key=key, ttl_minutes=30)
            runs.append((run_id, {"Authorization": f"Bearer {token}"}))
    return runs


async def _idle_in_transaction() -> int:
    """The application role's sessions idle in an open transaction, seen as a superuser."""
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return int(
            await connection.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE usename = $1 AND state = 'idle in transaction'"
                " AND datname = current_database()",
                APP_ROLE,
            )
        )
    finally:
        await connection.close()


async def test_calls_held_at_their_provider_leave_the_pool_to_the_other_requests(
    pg_app: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope

    await _small_pool(monkeypatch)
    runs = await _runs_with_catalogue(tmp_path, monkeypatch, HELD)
    provider = HeldProvider()
    provider.install(monkeypatch)
    loop = asyncio.get_running_loop()

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        calls = [
            asyncio.create_task(
                client.post(
                    f"/api/v1/internal/runs/{run_id}/tools/{TOOL}", headers=headers, json={"metier": "x"}
                )
            )
            for run_id, headers in runs
        ]
        try:
            # Every call has reached the provider, or given up on the pool and answered.
            deadline = loop.time() + 30
            while provider.arrived + sum(call.done() for call in calls) < HELD:
                assert loop.time() < deadline, "calls neither reached the provider nor ended"
                await asyncio.sleep(0.01)
            ended = [call.result().text for call in calls if call.done()]
            assert provider.arrived == HELD, f"{provider.arrived} calls reached the provider: {ended}"

            # A fourth request, a read that needs a connection, is served at once.
            started = loop.time()
            ready = await asyncio.wait_for(client.get("/readyz"), timeout=POOL_TIMEOUT_S * 5)
            waited = loop.time() - started
            assert ready.status_code == 200, ready.text
            assert waited < POOL_TIMEOUT_S, f"/readyz waited {waited:.2f} s for a connection"
            assert await _idle_in_transaction() == 0, "a call holds a connection idle in transaction"
        finally:
            provider.release.set()
            answers = await asyncio.gather(*calls, return_exceptions=True)

    for answer in answers:
        assert isinstance(answer, httpx.Response), answer
        assert answer.status_code == 200 and answer.json()["status_code"] == 200, answer.text
    # Booked after the call, in a transaction that set the run token's scope again: under RLS, a line
    # written without it would have been refused.
    async with session_scope(orgs="*") as session:
        lines = (
            await session.execute(
                select(CostLedger.run_id, CostLedger.provider).where(CostLedger.kind == "tool")
            )
        ).all()
    assert sorted(lines) == sorted((run_id, "annuaire") for run_id, _ in runs)


async def test_the_scope_and_the_user_are_carried_to_the_next_transaction_only(pg_app: Any) -> None:
    """`release_connection` commits; the session's next transaction starts in the scope and with the
    user the last one had, as `set_config(..., true)` would have lost them; the one after does not."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import (
        allow_release_during_calls,
        name_the_user,
        outside_the_transaction,
        session_scope,
    )

    async with session_scope(orgs="*") as session:
        session.add_all([Organization(slug="a", name="A"), Organization(slug="b", name="B")])

    settings = "SELECT current_setting('app.current_orgs', true), current_setting('app.current_user', true)"
    async with session_scope(orgs=["a"]) as session:
        await name_the_user(session, "user-a")
        allow_release_during_calls(session)
        async with outside_the_transaction(session):
            assert not session.in_transaction()
        assert tuple((await session.execute(text(settings))).one()) == ("a", "user-a")
        visible = (await session.execute(select(Organization.slug))).scalars().all()
        assert visible == ["a"], "the next transaction reads under the carried scope"
        await session.commit()
        orgs, user = (await session.execute(text(settings))).one()
        assert (orgs or "", user or "") == ("", ""), "carried once: the scope stays transaction-local"
