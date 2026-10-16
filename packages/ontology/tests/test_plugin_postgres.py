# SPDX-License-Identifier: Apache-2.0
"""Are the plugin's tables REALLY isolated by organisation on PostgreSQL?

On SQLite there is no RLS: this test runs only against a real PostgreSQL (`CHOREGOS_TEST_DATABASE_URL`,
set by CI). The schema is the one of `python -m choregos_api.migrer` with the plugin installed — the
`ontology` branch included — and the session is the NON-superuser application role's, without which RLS
would not apply.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

PG_URL = os.environ.get("CHOREGOS_TEST_DATABASE_URL", "")
APP_ROLE, APP_PASSWORD = "choregos_app", "app"

pytestmark = pytest.mark.skipif(
    not PG_URL.startswith("postgresql"), reason="CHOREGOS_TEST_DATABASE_URL unset: no PostgreSQL"
)

TABLES = ("ontology_versions", "managed_objects")


def _app_url(url: str) -> str:
    scheme, rest = url.split("://", 1)
    _, host = rest.rsplit("@", 1)
    return f"{scheme}://{APP_ROLE}:{APP_PASSWORD}@{host}"


async def _admin(*statements: str) -> Any:
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        result = None
        for statement in statements:
            result = await connection.fetch(statement)
        return result
    finally:
        await connection.close()


@pytest.fixture
async def migrated_database(plugin: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    from alembic import command
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.migrer import configuration

    await _admin(
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER; END IF; END $$",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_ROLE}",
    )
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _app_url(PG_URL))
    reset_settings_cache()
    await db_session.dispose_engine()
    # `migrations/env.py` calls `asyncio.run()`: in a separate thread, outside the test's loop.
    await asyncio.to_thread(command.upgrade, configuration(), "heads")
    try:
        yield
    finally:
        await db_session.dispose_engine()
        await _admin("DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        reset_settings_cache()


async def test_the_plugin_tables_are_under_forced_rls(migrated_database: None) -> None:
    rows = await _admin(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        f"WHERE relname IN ({', '.join(repr(t) for t in TABLES)})"
    )
    assert {(r["relname"], r["relrowsecurity"], r["relforcerowsecurity"]) for r in rows} == {
        (table, True, True) for table in TABLES
    }


async def test_an_organisation_sees_neither_the_objects_nor_the_ontology_of_another(
    migrated_database: None,
) -> None:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_ontology.service.store import ManagedObject, OntologyVersion
    from sqlalchemy import select

    projects: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = Organization(slug=slug, name=slug.upper())
            session.add(org)
            await session.flush()
            project = Project(org_id=org.id, slug="infra", name="Infra", status="active", config={})
            session.add(project)
            await session.flush()
            projects[slug] = project.id
            session.add(
                OntologyVersion(
                    project_id=project.id,
                    name="core-ref",
                    version="0.1.0",
                    checksum="sha256:x",
                    compiled_ir={},
                )
            )
            session.add(
                ManagedObject(
                    project_id=project.id,
                    object_type="finding",
                    id=f"os-{slug}",
                    properties={"key": f"os-{slug}"},
                )
            )

    async with session_scope("a") as session:
        objects = (await session.execute(select(ManagedObject.id))).scalars().all()
        versions = (await session.execute(select(OntologyVersion.project_id))).scalars().all()
    assert objects == ["os-a"]
    assert versions == [projects["a"]]

    async with session_scope() as session:
        assert (await session.execute(select(ManagedObject.id))).scalars().all() == [], "fail-closed"

    # A session of `a` cannot write an object into `b`'s project.
    with pytest.raises(Exception, match="row-level security"):
        async with session_scope("a") as session:
            session.add(
                ManagedObject(project_id=projects["b"], object_type="finding", id="intruder", properties={})
            )


async def test_onto0003_copies_the_proposals_under_forced_rls(
    plugin: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """onto0003's copy runs as the application's role, tables under forced RLS: without
    `set_config('app.current_orgs', '*', true)`, it would read no proposal — silently."""
    import json

    from alembic import command
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.migrer import configuration

    await _admin(
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER; END IF; END $$",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_ROLE}",
    )
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _app_url(PG_URL))
    reset_settings_cache()
    await db_session.dispose_engine()
    ir = json.dumps({"action_types": [{"name": "open_infra_pr", "effects": [{}], "evidence": []}]})
    try:
        await asyncio.to_thread(command.upgrade, configuration(), "f8a0b2c4d6e9")
        await asyncio.to_thread(command.upgrade, configuration(), "onto0002")
        from choregos_api.db.session import session_scope
        from choregos_ontology.service.store import OntologyVersion
        from sqlalchemy import text

        async with session_scope(orgs="*") as session:
            # Raw SQL, not the ORM: the database stands at `onto0002`, and the models describe the
            # CURRENT schema (every core column added since would make the INSERT fail).
            await session.execute(
                text(
                    "INSERT INTO organizations (id, slug, name, settings) "
                    "VALUES ('o1', 'varga', 'Varga', '{}')"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO projects (id, org_id, slug, name, status, config, provision_state) "
                    "VALUES ('p1', 'o1', 'infra', 'Infra', 'active', '{}', '{}')"
                )
            )
            session.add(
                OntologyVersion(
                    id="v1",
                    project_id="p1",
                    name="it4it",
                    version="1",
                    checksum="sha",
                    compiled_ir=json.loads(ir),
                )
            )
            await session.flush()
            columns = (
                "id, project_id, version_id, action_type, target_ids, params, justification, status, "
                "proposed_by, approval, decisions, effects, evidence"
            )
            values = (
                "'a1', 'p1', 'v1', 'open_infra_pr', '[]', '{}', 'x', 'pending_approval', '{}', '{}', "
                "'[]', '[]', '[]'"
            )
            await session.execute(text(f"INSERT INTO action_proposals ({columns}) VALUES ({values})"))
        await db_session.dispose_engine()
        await asyncio.to_thread(command.upgrade, configuration(), "heads")
        rows = await _admin("SELECT id, origin, status, effects FROM actions")
        assert [(r["id"], r["origin"], r["status"]) for r in rows] == [("a1", "ontology", "pending_approval")]
        effects = json.loads(rows[0]["effects"])
        assert effects == [{"effect": "ontology.effet", "with": {"index": 0}}]  # lang: fr-ok - persisted kind
        remaining = await _admin("SELECT to_regclass('public.action_proposals') AS table")
        assert remaining[0]["table"] is None, "the table is removed"
    finally:
        await db_session.dispose_engine()
        await _admin("DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        reset_settings_cache()
