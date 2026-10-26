"""Every migration, down and up again, on a real PostgreSQL (S40-22c).

`test_migrations.py` plays the round trip on SQLite only, and the PostgreSQL suite of the CI
(`CHOREGOS_TEST_SUITE_SUR_POSTGRES`) only ever goes up. A `downgrade` that SQLite accepts can fail
on PostgreSQL (an enum type left behind, a policy that still names a dropped column, a batch
operation that does not exist there): nothing ran one before this test. It runs whenever
`CHOREGOS_TEST_DATABASE_URL` names a PostgreSQL, as the CI's `python` job does.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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


async def _postgres_only_missing() -> set[str]:
    """What of `POSTGRES_ONLY` the migrated base lacks: the comparison skips them by name, this does
    not. `embedding_vec` is expected only where the image carries pgvector."""
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        columns = await connection.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'knowledge_chunks'"
        )
        indexes = await connection.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'knowledge_chunks'"
        )
        vector = await connection.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
    finally:
        await connection.close()
    expected = POSTGRES_ONLY if vector else POSTGRES_ONLY - {"embedding_vec"}
    return expected - {row["column_name"] for row in columns} - {row["indexname"] for row in indexes}


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
        assert not await _postgres_only_missing(), "skipped by the comparison, not absent"

        await asyncio.to_thread(command.downgrade, config, "base")
        assert await _tables() <= {"alembic_version"}, "every table must be dropped on the way down"
        assert not await _types(), "an enum type outlived the migration that created it"

        await asyncio.to_thread(command.upgrade, config, "heads")
        assert not await _compare(), "the schema after a round trip is not the models' schema"
        assert not await _postgres_only_missing(), "skipped by the comparison, not absent"
    finally:
        await db_session.dispose_engine()
        await _administrer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        reset_settings_cache()


# ───────────────────────────── over data (S40-22d) ─────────────────────────────

#: The latest migrations, newest first, each with what its downgrade drops. Seeded at the newest,
#: walked down one step at a time over the rows, then up again to the heads: every row a migration
#: did not create must survive both ways, and what it created goes with its downgrade, as its
#: docstring says.
WINDOW: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("d9f1b3c5e7a2", ("a2a_push_configs",)),
    ("c7e9a1b3d5f8", ("runs.inputs_from", "runs.initiated_by", "work_items.initiated_by")),
    ("b7d1f3a5c9e2", ("connector_operations.result_max_bytes",)),
    ("e4a6c8d0b2f1", ("knowledge_grants", "knowledge_bases.restricted")),
    ("b3c5d7e9f1a2", ("knowledge_distillation_parts",)),
)

#: One row in each table the window touches, and in what they hang from, in the order of their
#: foreign keys. A JSON value is a string: asyncpg sends it as `jsonb` text.
NOW = datetime.now(UTC)
SEED: tuple[tuple[str, dict[str, Any]], ...] = (
    ("organizations", {"id": "o1", "slug": "varga", "name": "Varga", "settings": "{}"}),
    ("users", {"id": "u1", "email": "ada@example.org", "display_name": "Ada", "is_service": False}),
    (
        "projects",
        {
            "id": "p1",
            "org_id": "o1",
            "slug": "billing-api",
            "name": "Billing",
            "config": "{}",
            "status": "active",
        }
        | {"provision_state": "{}"},
    ),
    (
        "work_items",
        {
            "id": "w1",
            "project_id": "p1",
            "tracker_key": "BIL-1",
            "title": "Add credit notes",
            "state": "build",
        }
        | {"paused": False, "allowed_paths": "[]", "totals": "{}", "documents": "[]"}
        | {"initiated_by": "a2a:ada@example.org"},
    ),
    (
        "runs",
        {"id": "r1", "work_item_id": "w1", "project_id": "p1", "stage_role": "builder", "attempt": 1}
        | {"status": "succeeded", "tokens": "{}", "cost_usd": 0.25, "allowed_paths": "[]"}
        | {"initiated_by": "a2a:ada@example.org", "inputs_from": '["r0"]'},
    ),
    (
        "org_connectors",
        {"id": "c1", "org_id": "o1", "name": "tracker", "kind": "mcp", "type": "internal", "config": "{}"}
        | {"status": "ok", "created_at": NOW, "updated_at": NOW},
    ),
    (
        "connector_operations",
        {
            "id": "op1",
            "org_id": "o1",
            "connector_id": "c1",
            "name": "search",
            "access": "read",
            "policy": "allow",
        }
        | {"groups": "[]", "created_at": NOW, "updated_at": NOW, "result_max_bytes": 4096},
    ),
    ("api_tokens", {"id": "t1", "user_id": "u1", "name": "partner", "hash": "h", "scopes": '["a2a"]'}),
    (
        "a2a_push_configs",
        {"id": "pc1", "org_id": "o1", "work_item_id": "w1", "api_token_id": "t1", "client": "partner"}
        | {"config_id": "cfg", "url": "https://hooks.example.org/a2a", "attempts": 0},
    ),
    (
        "knowledge_bases",
        {
            "id": "kb1",
            "org_id": "o1",
            "slug": "handbook",
            "name": "Handbook",
            "scope": "org",
            "projects": "[]",
        }
        | {"groups": "[]", "serving_mode": "files", "status": "active", "restricted": True},
    ),
    (
        "knowledge_grants",
        {"id": "g1", "org_id": "o1", "knowledge_base_id": "kb1", "subject_type": "person"}
        | {"subject": "ada@example.org", "role": "reader"},
    ),
    (
        "knowledge_documents",
        {"id": "d1", "org_id": "o1", "knowledge_base_id": "kb1", "source": "upload", "filename": "ledger.md"}
        | {
            "title": "Ledger",
            "mime": "text/markdown",
            "size_bytes": 20,
            "digest": "sha256:0",
            "status": "indexed",
        },
    ),
    (
        "knowledge_chunks",
        {"id": "k1", "org_id": "o1", "knowledge_base_id": "kb1", "document_id": "d1", "ordinal": 0}
        | {"heading_path": "[]", "body": "Credit notes ledger", "text": "credit notes ledger"},
    ),
    (
        "knowledge_distillation_parts",
        {"id": "dp1", "org_id": "o1", "knowledge_base_id": "kb1", "digest": "sha256:1", "model": "model"}
        | {"part_index": 0, "documents": "[]", "characters": 20, "cost_usd": 0.01, "status": "done"},
    ),
)


async def _seed(rows: tuple[tuple[str, dict[str, Any]], ...] = SEED) -> None:
    """As the superuser: the rows are the test's, not a request's, and RLS does not apply."""
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        for table, row in rows:
            places = ", ".join(f"${i}" for i in range(1, len(row) + 1))
            await connection.execute(
                f"INSERT INTO {table} ({', '.join(row)}) VALUES ({places})", *row.values()
            )
    finally:
        await connection.close()


#: What no migration of the window created: it must read the same at every step, down and up.
KEPT = (
    "SELECT title, state FROM work_items WHERE id = 'w1'",
    "SELECT status, cost_usd FROM runs WHERE id = 'r1'",
    "SELECT name, access FROM connector_operations WHERE id = 'op1'",
    "SELECT name, scopes::text FROM api_tokens WHERE id = 't1'",
    "SELECT slug, scope FROM knowledge_bases WHERE id = 'kb1'",
    "SELECT text FROM knowledge_chunks WHERE id = 'k1' AND search_tsv @@ to_tsquery('simple', 'ledger')",
)

#: The chunk's vector, read the same way at every step (S40-22e): the window must not touch it.
EMBEDDING = "SELECT embedding_vec::text FROM knowledge_chunks WHERE id = 'k1' AND embedding_vec IS NOT NULL"


async def _fetch(*queries: str) -> list[list[tuple[Any, ...]]]:
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return [[tuple(row) for row in await connection.fetch(query)] for query in queries]
    finally:
        await connection.close()


async def _columns(table: str) -> set[str]:
    [rows] = await _fetch(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'")
    return {str(row[0]) for row in rows}


async def _dropped(what: str) -> bool:
    table, _, column = what.partition(".")
    if column:
        return column not in await _columns(table)
    return table not in await _tables()


async def test_the_latest_migrations_go_down_and_up_again_over_data(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic.script import ScriptDirectory
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.migrer import configuration

    await _preparer_postgres()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _url_app(PG_URL))
    reset_settings_cache()
    await db_session.dispose_engine()
    config = configuration()
    scripts = ScriptDirectory.from_config(config)
    try:
        await asyncio.to_thread(command.upgrade, config, WINDOW[0][0])
        await _seed()
        vector = "embedding_vec" in await _columns("knowledge_chunks")
        if vector:  # S40-22e: only where the image carries pgvector, as `_postgres_only_missing` says
            await _administrer(
                PG_URL, "UPDATE knowledge_chunks SET embedding_vec = '[0.25,0.5,1]' WHERE id = 'k1'"
            )
        queries = (*KEPT, EMBEDDING) if vector else KEPT
        kept = await _fetch(*queries)
        assert all(kept), "every seeded row reads back, the chunk found by its generated `search_tsv`"

        for revision, drops in WINDOW:
            script = scripts.get_revision(revision)
            assert script is not None and "downgrade" in (script.module.__doc__ or "").lower(), (
                f"{revision} drops {', '.join(drops)} on the way down: its docstring says so"
            )
            await asyncio.to_thread(command.downgrade, config, str(script.down_revision))
            for what in drops:
                assert await _dropped(what), f"{what} outlived the downgrade of {revision}"
            assert await _fetch(*queries) == kept, (
                f"the downgrade of {revision} lost or changed a row it did not create"
            )

        await asyncio.to_thread(command.upgrade, config, "heads")
        assert await _fetch(*queries) == kept, "the upgrade back to the heads lost or changed a row"
        assert not await _compare(), "the schema after the round trip over data is not the models' schema"
        chain, cap, restricted, created = await _fetch(
            "SELECT w.initiated_by, r.initiated_by, r.inputs_from"
            " FROM work_items w JOIN runs r ON r.work_item_id = w.id",
            "SELECT result_max_bytes FROM connector_operations WHERE id = 'op1'",
            "SELECT restricted FROM knowledge_bases WHERE id = 'kb1'",
            "SELECT (SELECT count(*) FROM a2a_push_configs) + (SELECT count(*) FROM knowledge_grants)"
            " + (SELECT count(*) FROM knowledge_distillation_parts)",
        )
        assert chain == [(None, None, None)], "no backfill: the access record says the initiator is unknown"
        assert cap == [(None,)], "no backfill: the operation is back to the platform's default cap"
        assert restricted == [(False,)], "an unrestricted base, as before the grants existed"
        assert created == [(0,)], "what a downgrade dropped does not come back"
    finally:
        await db_session.dispose_engine()
        await _administrer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        reset_settings_cache()


# ───────────────────────────── as the migration job (S40-22e) ─────────────────────────────
#
# The chart's Job migrates as the API's own role (`tests/charts/test_hook_de_migration.py`), under
# forced RLS: a data step sees only the organisations the session opened. Every test here migrates
# as that role, over rows seeded by the superuser, and reads the result back as the superuser.


async def _migrate_as_the_job(monkeypatch: pytest.MonkeyPatch) -> Any:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.migrer import configuration

    await _preparer_postgres()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _url_app(PG_URL))
    reset_settings_cache()
    await db_session.dispose_engine()
    return configuration()


async def _clean() -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session

    await db_session.dispose_engine()
    await _administrer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
    reset_settings_cache()


async def test_the_migration_role_is_held_by_rls(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the tests below rely on: the role the Job uses sees no row of an organisation it did
    not open, and every row once it opens them all, as the data steps do."""
    import asyncpg

    config = await _migrate_as_the_job(monkeypatch)
    try:
        await asyncio.to_thread(command.upgrade, config, "heads")
        await _seed(SEED[:3])
        connection = await asyncpg.connect(_url_app(PG_URL).replace("postgresql+asyncpg://", "postgresql://"))
        try:
            role = await connection.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            unseen = await connection.fetchval("SELECT count(*) FROM projects")
            async with connection.transaction():
                await connection.execute("SELECT set_config('app.current_orgs', '*', true)")
                opened = await connection.fetchval("SELECT count(*) FROM projects")
        finally:
            await connection.close()
        assert tuple(role) == (False, False), "the migration role neither is a superuser nor bypasses RLS"
        assert (unseen, opened) == (0, 1)
    finally:
        await _clean()


#: An older data migration (2026-10-05): two active versions of one workflow are folded into the
#: newest, which the project names as its default and its unpinned work items pin.
PINNED_BEFORE = "f6b8d0e2a4c6"
PINNED = "a7c9e1f3b5d8"
PINNED_SEED: tuple[tuple[str, dict[str, Any]], ...] = (
    SEED[0],
    (
        "projects",
        {"id": "p1", "org_id": "o1", "slug": "billing-api", "name": "Billing", "config": "{}"}
        | {"status": "active", "provision_state": "{}"},
    ),
    *(
        (
            "workflow_defs",
            {"id": f"wf{version}", "project_id": "p1", "name": "default", "version": version}
            | {
                "source": "yaml",
                "yaml": "",
                "json": "{}",
                "checksum": f"sha256:{version}",
                "is_active": True,
            },
        )
        for version in (1, 2)
    ),
    (
        "work_items",
        {
            "id": "w1",
            "project_id": "p1",
            "tracker_key": "BIL-1",
            "title": "Add credit notes",
            "state": "build",
        }
        | {"paused": False, "allowed_paths": "[]", "totals": "{}", "documents": "[]"},
    ),
)


async def test_an_older_data_migration_rewrites_rows_under_rls(monkeypatch: pytest.MonkeyPatch) -> None:
    config = await _migrate_as_the_job(monkeypatch)
    try:
        await asyncio.to_thread(command.upgrade, config, PINNED_BEFORE)
        await _seed(PINNED_SEED)
        await asyncio.to_thread(command.upgrade, config, PINNED)
        active, default, pinned = await _fetch(
            "SELECT id FROM workflow_defs WHERE is_active ORDER BY id",
            "SELECT default_workflow FROM projects WHERE id = 'p1'",
            "SELECT workflow_def_id FROM work_items WHERE id = 'w1'",
        )
        assert active == [("wf2",)], "the older active version is retired, as a request would see it"
        assert default == [("default",)] and pinned == [("wf2",)], "the rows were rewritten, not skipped"
    finally:
        await _clean()


#: The release channels' downgrade (2026-10-17) deletes the pins a channel carries before the
#: version becomes required again.
CHANNELS = "c1e3a5b7d9f2"
CHANNELS_SEED: tuple[tuple[str, dict[str, Any]], ...] = (
    *PINNED_SEED[:2],
    *(
        (
            "agents",
            {"id": agent, "org_id": "o1", "slug": agent, "kind": "internal", "display_name": agent}
            | {"status": "active"},
        )
        for agent in ("builder", "reviewer")
    ),
    (
        "agent_versions",
        {
            "id": "av1",
            "agent_id": "builder",
            "org_id": "o1",
            "version": 1,
            "spec": "{}",
            "checksum": "sha256:1",
        },
    ),
    *(
        (
            "project_agents",
            {"id": pin, "project_id": "p1", "org_id": "o1", "agent_id": agent, "overrides": "{}"} | ref,
        )
        for pin, agent, ref in (
            ("pa-version", "builder", {"version": 1}),
            ("pa-channel", "reviewer", {"channel": "stable"}),
        )
    ),
)


async def test_a_downgrade_deletes_rows_under_rls(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic.script import ScriptDirectory

    config = await _migrate_as_the_job(monkeypatch)
    try:
        await asyncio.to_thread(command.upgrade, config, CHANNELS)
        await _seed(CHANNELS_SEED)
        script = ScriptDirectory.from_config(config).get_revision(CHANNELS)
        assert script is not None
        await asyncio.to_thread(command.downgrade, config, str(script.down_revision))
        [pins] = await _fetch("SELECT id, version FROM project_agents ORDER BY id")
        assert pins == [("pa-version", 1)], "the channel pin is deleted, the version pin kept"
    finally:
        await _clean()
