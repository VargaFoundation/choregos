"""Every migration, down and up again, on a real PostgreSQL (S40-22c).

`test_migrations.py` plays the round trip on SQLite only, and the PostgreSQL suite of the CI
(`CHOREGOS_TEST_SUITE_SUR_POSTGRES`) only ever goes up. A `downgrade` that SQLite accepts can fail
on PostgreSQL (an enum type left behind, a policy that still names a dropped column, a batch
operation that does not exist there): nothing ran one before this test. It runs whenever
`CHOREGOS_TEST_DATABASE_URL` names a PostgreSQL, as the CI's `python` job does.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from choregos_api.db.models import Base, objet_du_coeur
from sqlalchemy.ext.asyncio import create_async_engine

from .conftest import PG_URL, _administrer, _preparer_postgres, _url_app, sans_postgres

#: `embedding_vec` is a pgvector column the reflection does not know: said once, not a failure.
pytestmark = [sans_postgres, pytest.mark.filterwarnings("ignore:Did not recognize type 'vector'")]

#: What the comparison reports that is a dialect detail, not a schema disagreement.
DIALECT_NOISE = ("modify_type", "modify_default", "modify_nullable", "modify_comment")

#: What the knowledge index migration adds on PostgreSQL only and the ORM does not describe, on
#: purpose (`KnowledgeChunk`, ADR 0051): the generated `search_tsv`, its GIN index, `embedding_vec`.
POSTGRES_ONLY = {"search_tsv", "ix_knowledge_chunks_search_tsv", "embedding_vec"}


def _postgres_only(difference: Any) -> bool:
    if not isinstance(difference, tuple):
        return False
    if difference[0] == "remove_index":
        return str(difference[1].name) in POSTGRES_ONLY
    if difference[0] == "remove_column":
        return str(difference[3].name) in POSTGRES_ONLY
    return False


def _differences(connection: Any) -> list[Any]:
    context = MigrationContext.configure(connection, opts={"include_object": objet_du_coeur})
    return [
        difference
        for difference in compare_metadata(context, Base.metadata)
        if not (isinstance(difference, list) and difference and str(difference[0][0]) in DIALECT_NOISE)
        and not (isinstance(difference, tuple) and str(difference[0]) in DIALECT_NOISE)
        and not _postgres_only(difference)
    ]


async def _tables() -> set[str]:
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        rows = await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    finally:
        await connection.close()
    return {row["tablename"] for row in rows}


async def _types() -> set[str]:
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        rows = await connection.fetch(
            "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = 'public' AND t.typtype = 'e'"
        )
    finally:
        await connection.close()
    return {row["typname"] for row in rows}


async def _compare() -> list[Any]:
    engine = create_async_engine(_url_app(PG_URL))
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_differences)
    finally:
        await engine.dispose()


async def test_every_migration_goes_down_and_up_again_on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.migrer import configuration

    await _preparer_postgres()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _url_app(PG_URL))
    reset_settings_cache()
    await db_session.dispose_engine()
    config = configuration()
    try:
        # `migrations/env.py` calls `asyncio.run()`, refused inside a running loop: a thread each.
        await asyncio.to_thread(command.upgrade, config, "heads")
        assert {"runs", "work_items"} <= await _tables()
        assert not await _compare(), "the migrated PostgreSQL schema is not the models' schema"

        await asyncio.to_thread(command.downgrade, config, "base")
        assert await _tables() <= {"alembic_version"}, "every table must be dropped on the way down"
        assert not await _types(), "an enum type outlived the migration that created it"

        await asyncio.to_thread(command.upgrade, config, "heads")
        assert not await _compare(), "the schema after a round trip is not the models' schema"
    finally:
        await db_session.dispose_engine()
        await _administrer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        reset_settings_cache()
