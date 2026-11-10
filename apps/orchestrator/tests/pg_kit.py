"""PostgreSQL helpers of the orchestrator suite: a migrated schema and the application role.

Copied (minimal, in English) from the API suite's conftest: test packages do not import each other.
A superuser ignores RLS whatever the policies say, so the suite connects as a non-superuser role,
which is what the deployment guarantees too.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

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
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER; END IF; "
        "EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL; END $$",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_ROLE}",
    )
    # pgvector is not a trusted extension: a superuser creates it, never the application role.
    # An image without it is a case under test too (the knowledge index serves full-text only).
    with contextlib.suppress(Exception):
        await administer(url, "CREATE EXTENSION IF NOT EXISTS vector")


async def migrate() -> None:
    """The schema by the migration command of the chart's Job (every head)."""
    from choregos_api.migrate import configuration

    await asyncio.to_thread(command.upgrade, configuration(), "heads")


#: What the schema looks like once Alembic has built it: relations, functions, policies, extensions and
#: the revision. Taken once per process; a test that drops, adds or downgrades anything makes the
#: next `reset()` differ from it and rebuild.
_SCHEMA_FINGERPRINT = """
SELECT coalesce(md5(string_agg(item, ',' ORDER BY item)), '') FROM (
    SELECT 'r' || c.relkind::text || c.relname AS item FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'
    UNION ALL SELECT 'f' || p.proname FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'public'
    UNION ALL SELECT 'p' || polrelid::regclass::text || polname FROM pg_policy
    UNION ALL SELECT 'e' || extname FROM pg_extension
    UNION ALL SELECT 'v' || version_num FROM alembic_version
) AS parts
"""

#: Fails when a migration leaves rows behind in a fresh database (outside `alembic_version`).
_FAIL_ON_MIGRATED_ROWS = """
DO $$ DECLARE name text; found boolean; BEGIN
    FOR name IN SELECT tablename FROM pg_tables
        WHERE schemaname = 'public' AND tablename <> 'alembic_version' LOOP
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM public.%I)', name) INTO found;
        IF found THEN RAISE EXCEPTION 'rows in %', name USING ERRCODE = 'P0001'; END IF;
    END LOOP;
END $$
"""

#: Every table but `alembic_version` emptied, sequences restarted: what dropping the schema and
#: migrating again leaves, in one round trip instead of ninety-five migrations.
_EMPTY_EVERY_TABLE = """
DO $$ DECLARE tables text; BEGIN
    SELECT string_agg(format('%I.%I', schemaname, tablename), ', ') INTO tables
        FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version';
    IF tables IS NOT NULL THEN EXECUTE 'TRUNCATE ' || tables || ' RESTART IDENTITY CASCADE'; END IF;
END $$
"""

#: Empty until this process has built the schema once; `False` when a migration seeds rows, which
#: emptying the tables would remove: every test then rebuilds the schema, as it did before.
_built: dict[str, str | bool] = {}


async def _seeded(url: str) -> bool:
    import asyncpg

    try:
        await administer(url, _FAIL_ON_MIGRATED_ROWS)
    except asyncpg.exceptions.RaiseError:
        return True
    return False


async def _query(url: str, statement: str) -> Any:
    import asyncpg

    connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return await connection.fetchval(statement)
    finally:
        await connection.close()


async def reset(url: str) -> None:
    """An empty database, migrated by Alembic, the non-superuser role owning it.

    The schema is built once per process (95 migrations, about four seconds) and the following tests
    empty its tables. A test that drops or changes the schema makes it differ from what was built: the
    next call rebuilds it. The caller has set `CHOREGOS_DATABASE_URL` to `app_url(url)` and has reset
    the settings and the engine.
    """
    fingerprint = _built.get("fingerprint")
    if isinstance(fingerprint, str):
        try:
            current = await _query(url, _SCHEMA_FINGERPRINT)
        except Exception:  # schema dropped: no `alembic_version`
            current = None
        if current == fingerprint:
            await administer(url, _EMPTY_EVERY_TABLE)
            return
    await prepare(url)
    await migrate()
    seeded = await _seeded(url)
    _built["fingerprint"] = False if seeded else await _query(url, _SCHEMA_FINGERPRINT)
