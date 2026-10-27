"""An installed plugin and its files are an organisation's, under forced RLS (S43-04, ADR 0093).

Runs only against a real PostgreSQL (`CHOREGOS_TEST_DATABASE_URL`), migrated by Alembic and seen by the
non-superuser application role: the migration's policies are what is under test.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .conftest import sans_postgres

pytestmark = sans_postgres


async def test_a_plugin_row_and_its_files_are_invisible_to_another_organisation(pg_app: Any) -> None:
    from choregos_api.db.models import Organization, OrgPlugin, OrgPluginFile
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = Organization(slug=slug, name=slug.upper())
            session.add(org)
            await session.flush()
            session.add(
                OrgPlugin(org_id=org.id, name=f"p-{slug}", source="offline", version="1.0.0",
                          digest=f"sha256:{slug * 64}", manifest={}, contents={}, details={})
            )  # fmt: skip
            session.add(OrgPluginFile(org_id=org.id, digest=f"sha256:{slug * 64}", path="x.md", content=b"x"))

    async with session_scope() as session:  # no scope: nothing
        assert (await session.execute(select(OrgPlugin))).scalars().all() == []
        assert (await session.execute(select(OrgPluginFile))).scalars().all() == []
    async with session_scope(orgs=["a"]) as session:
        assert [p.name for p in (await session.execute(select(OrgPlugin))).scalars()] == ["p-a"]
        assert [f.digest for f in (await session.execute(select(OrgPluginFile))).scalars()] == [
            "sha256:" + "a" * 64
        ]
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(OrgPlugin))).scalars().all()) == 2


async def test_a_waiting_plugin_approval_is_invisible_to_another_organisation(pg_app: Any) -> None:
    from choregos_api.db.models import Organization, OrgPluginApproval
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = Organization(slug=slug, name=slug.upper())
            session.add(org)
            await session.flush()
            session.add(
                OrgPluginApproval(org_id=org.id, action="install", source="offline", name=f"p-{slug}",
                                  version="1.0.0", digest=f"sha256:{slug * 64}", reason="a server",
                                  status="pending")
            )  # fmt: skip

    async with session_scope() as session:  # no scope: nothing
        assert (await session.execute(select(OrgPluginApproval))).scalars().all() == []
    async with session_scope(orgs=["b"]) as session:
        assert [p.name for p in (await session.execute(select(OrgPluginApproval))).scalars()] == ["p-b"]
