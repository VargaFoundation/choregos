"""Root conftest: one PostgreSQL database per pytest-xdist worker.

The suites that run on PostgreSQL (`CHOREGOS_TEST_DATABASE_URL`) reset the schema of the database
they are given, so two workers cannot share one. Each worker takes its own, `<name>_gw0`,
`<name>_gw1`, ... created beside the configured one, and the variable is rewritten before any
test module reads it (`PG_URL` constants are evaluated at import). Without xdist nothing changes.

It runs when this file is imported, not in `pytest_configure`: the conftests of the directories
named on the command line are imported before any `pytest_configure`, and they read the variable.
"""

from __future__ import annotations

import asyncio
import os

#: The advisory lock that serialises `CREATE DATABASE`: copying `template1` fails when another
#: session creates a database from it at the same moment.
_CREATE_DATABASE_LOCK = 4_204_201


async def _ensure_database(url: str, name: str) -> None:
    import asyncpg

    connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await connection.execute(f"SELECT pg_advisory_lock({_CREATE_DATABASE_LOCK})")
        if not await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            await connection.execute(f'CREATE DATABASE "{name}"')
        await connection.execute(f"SELECT pg_advisory_unlock({_CREATE_DATABASE_LOCK})")
    finally:
        await connection.close()


def _own_database_per_worker() -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    url = os.environ.get("CHOREGOS_TEST_DATABASE_URL", "")
    # Imported twice (a second import path), it must not suffix the name twice.
    if (
        not worker
        or not url.startswith("postgresql")
        or os.environ.get("CHOREGOS_TEST_DATABASE_WORKER") == worker
    ):
        return
    from sqlalchemy.engine import make_url

    own = make_url(url).set(database=f"{make_url(url).database}_{worker}")
    asyncio.run(_ensure_database(url, str(own.database)))
    os.environ["CHOREGOS_TEST_DATABASE_URL"] = own.render_as_string(hide_password=False)
    os.environ["CHOREGOS_TEST_DATABASE_WORKER"] = worker


_own_database_per_worker()
