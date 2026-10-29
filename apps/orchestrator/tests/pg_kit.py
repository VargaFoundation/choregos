"""PostgreSQL helpers of the orchestrator suite: a migrated schema and the application role.

Copied (minimal, in English) from the API suite's conftest: test packages do not import each other.
A superuser ignores RLS whatever the policies say, so the suite connects as a non-superuser role,
which is what the deployment guarantees too.
"""

from __future__ import annotations

import asyncio
import contextlib

from alembic import command

APP_ROLE, APP_PASSWORD = "choregos_app", "app"


def app_url(url: str) -> str:
    """The same database, seen by the non-superuser application role."""
    scheme, rest = url.split("://", 1)
    _, host = rest.rsplit("@", 1)
    return f"{scheme}://{APP_ROLE}:{APP_PASSWORD}@{host}"


async def administer(url: str, *statements: str) -> None:
    """Run statements as the superuser, outside any SQLAlchemy engine."""
    import asyncpg

    connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.close()


async def prepare(url: str) -> None:
    """Empty schema, the non-superuser application role, and pgvector when the image has it."""
    await administer(
        url,
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER; END IF; END $$",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_ROLE}",
    )
    # pgvector is not a trusted extension: a superuser creates it, never the application role.
    # An image without it is a case under test too (the knowledge index serves full-text only).
    with contextlib.suppress(Exception):
        await administer(url, "CREATE EXTENSION IF NOT EXISTS vector")


async def migrate() -> None:
    """The schema by the migration command of the chart's Job (every head)."""
    from choregos_api.migrer import configuration

    await asyncio.to_thread(command.upgrade, configuration(), "heads")
