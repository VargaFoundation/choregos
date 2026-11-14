# SPDX-License-Identifier: Apache-2.0
"""A write through an outpost on a real PostgreSQL (S65-14, ADR 0149 §5-6).

What SQLite cannot show. Two retries of one effect that place its write at the same instant are two
sessions on two connections: the second waits for the first under the database's own lock, then
meets its row, and the table refuses a second row for the id whoever writes it. The two statements
of an operation's rule are columns of the migrated schema, with the defaults the migration gives
the rows that were there.

Runs only with `CHOREGOS_TEST_DATABASE_URL`: the schema is the migration's, the role is not a
superuser, and no session has a scope it did not ask for (`pg_app`). The rest of the story's proofs
run here too when the whole suite does (`CHOREGOS_TEST_SUITE_ON_POSTGRES`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from .conftest import PG_URL, login, without_postgres
from .kits import outpost_daemon as daemon_kit
from .kits import outpost_route as route
from .kits import outpost_writes as kit
from .kits import outposts as outposts_kit
from .kits.outpost_route import ALIAS, ORG, SLUG
from .kits.outpost_writes import ACCEPTED

pytestmark = without_postgres

TITLE = {"title": "door stuck"}


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> outposts_kit.Door:
    return route.switch_on(monkeypatch)


@pytest.fixture
async def client(pg_app: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """The application on the migrated schema, an organisation and its administrator signed in."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", outposts_kit.ORG)
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
    reset_settings_cache()
    async with session_scope(orgs="*") as session:
        session.add(Organization(slug=outposts_kit.ORG, name="Varga Foundation"))
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as http:
        await login(http, "admin@varga.dev")
        yield http


@pytest.fixture
async def project(client: AsyncClient) -> dict[str, Any]:
    payload = {
        "slug": "billing-api",
        "name": "Billing API",
        "config": {
            "slug": "billing-api",
            "org": outposts_kit.ORG,
            "repo": {
                "url": "https://github.com/varga/billing-api.git",
                "default_branch": "main",
                "language": "python",
                "test_command": "make test",
            },
        },
    }
    response = await client.post(f"{ORG}/projects", json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _as_the_platform(statement: str, *arguments: Any) -> Any:
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return await connection.fetch(statement, *arguments)
    finally:
        await connection.close()


async def test_two_retries_that_place_one_write_at_once_place_it_once(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The id of a write is its effect's, so two attempts of the effect place the same id. The
    second arrives while the first has written its row and not committed: it waits, then meets that
    row and is told so. And without any lock the table holds an id once."""
    import asyncpg
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous
    from choregos_api.services.outpost_route import write_call_id

    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    connector = await route.connector_row()
    outpost = await outposts_kit.outpost_row(SLUG)
    call_id = write_call_id(connector, "a-1:0")
    write: dict[str, Any] = {
        "call_id": call_id,
        "kind": rendezvous.OutpostCallKind.CALL_TOOL,
        "endpoint": ALIAS,
        "timeout_s": 30,
        "write": True,
        "tool": "create_issue",
        "arguments": TITLE,
        "connector_id": connector.id,
    }
    async with session_scope(orgs=[outposts_kit.ORG]) as first:
        placed = await rendezvous.place(first, outpost, **write)
        assert placed.created
        # Written, not committed: the retry must not place a second row beside it.
        retry = asyncio.create_task(daemon_kit.place(**write))
        await asyncio.sleep(0.7)
        assert not retry.done(), "the second placement waits for the first to commit"
    met = await asyncio.wait_for(retry, timeout=10)
    assert (met.id, met.created, met.state) == (call_id, False, rendezvous.OutpostCallState.PENDING)
    (row,) = await kit.deliveries()
    assert (row.id, row.write, row.state) == (call_id, True, "pending")
    # The last line is the table's own: no lock and no row-level security stand between this
    # connection and it, and the id is still refused a second row.
    with pytest.raises(asyncpg.UniqueViolationError):
        await _as_the_platform("INSERT INTO outpost_calls SELECT * FROM outpost_calls WHERE id = $1", call_id)
    counted = await _as_the_platform("SELECT count(*) FROM outpost_calls WHERE id = $1", call_id)
    assert counted[0][0] == 1


async def test_the_migrated_schema_holds_the_two_statements_with_their_defaults(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        columns = {
            name: (kind, nullable, default)
            for name, kind, nullable, default in (
                await session.execute(
                    text(
                        "SELECT column_name, data_type, is_nullable, column_default "
                        "FROM information_schema.columns WHERE table_name = 'connector_operations' "
                        "AND column_name IN ('platform_arguments', 'in_doubt_accepted')"
                    )
                )
            ).all()
        }
    assert columns["in_doubt_accepted"] == ("boolean", "NO", "false")
    assert columns["platform_arguments"][1:] == ("YES", None)
    # A row written before the statements existed says neither: it is read as nothing stated.
    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake)
    await _as_the_platform(
        "UPDATE connector_operations SET platform_arguments = DEFAULT, in_doubt_accepted = DEFAULT "
        "WHERE name = 'create_issue'"
    )
    operation = await kit.operation_of(client, "create_issue")
    assert (operation["platform_arguments"], operation["in_doubt_accepted"]) == ([], False)
    assert (await kit.state(client, "create_issue", policy="approval")).status_code == 422
