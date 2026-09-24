"""La RLS PostgreSQL isole-t-elle VRAIMENT une organisation d'une autre ?

Jamais exercée avant le 2026-09-24 : toute la suite tournait sur SQLite, où la politique
n'existe pas, et la politique elle-même était fail-open. Ce test ne tourne que contre un
vrai PostgreSQL (`CHOREGOS_TEST_DATABASE_URL`) : la CI en lance un, en local
`docs/dev.md` dit comment.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import login

PG_URL = os.environ.get("CHOREGOS_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not PG_URL.startswith("postgresql"), reason="CHOREGOS_TEST_DATABASE_URL absent : pas de PostgreSQL"
)
API = pathlib.Path(__file__).resolve().parents[1]


APP_ROLE, APP_PASSWORD = "choregos_app", "app"


def _url_app(url: str) -> str:
    """La même base, vue par le rôle applicatif NON superutilisateur.

    Un superutilisateur ignore la RLS, quoi qu'on écrive dans les politiques. Le rôle du
    conteneur de test (et du service de la CI) en est un : tester avec lui prouverait que
    la RLS ne fait rien — et ce serait vrai. C'est aussi ce que le déploiement doit
    garantir : l'API ne se connecte jamais avec un superutilisateur.
    """
    scheme, rest = url.split("://", 1)
    _, hote = rest.rsplit("@", 1)
    return f"{scheme}://{APP_ROLE}:{APP_PASSWORD}@{hote}"


async def _administrer(url: str, *ordres: str) -> None:
    """Exécute des ordres en superutilisateur, hors de tout moteur SQLAlchemy."""
    import asyncpg

    connexion = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        for ordre in ordres:
            await connexion.execute(ordre)
    finally:
        await connexion.close()


@pytest.fixture
async def pg_app(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """L'application sur un PostgreSQL migré par Alembic (et non `create_all`), en rôle
    applicatif non superutilisateur, vidé à la fin."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.main import create_app
    from choregos_api.temporal import FakeTemporal, set_temporal

    await _administrer(
        PG_URL,
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER; END IF; END $$",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_ROLE}",
    )
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _url_app(PG_URL))
    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", "a")
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@a.test")
    reset_settings_cache()
    await db_session.dispose_engine()
    config = Config(str(API / "alembic.ini"))
    config.set_main_option("script_location", str(API / "migrations"))
    # `migrations/env.py` fait `asyncio.run()` : interdit depuis une boucle déjà en cours,
    # donc dans un fil à part. Les tables appartiennent au rôle applicatif : `FORCE ROW
    # LEVEL SECURITY` s'applique donc à lui aussi.
    await asyncio.to_thread(command.upgrade, config, "head")
    set_temporal(FakeTemporal())
    try:
        yield create_app()
    finally:
        await db_session.dispose_engine()
        await _administrer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        set_temporal(None)
        reset_settings_cache()


@pytest.fixture
async def deux_organisations(pg_app: Any) -> dict[str, str]:
    """`a` et `b`, chacune avec un projet `billing-api` et un ticket."""
    from choregos_api.db.models import Organization, Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults

    ids: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            org = Organization(slug=slug, name=slug.upper())
            session.add(org)
            await session.flush()
            projet = Project(
                org_id=org.id,
                slug="billing-api",
                name="Billing",
                status="active",
                config={"slug": "billing-api", "org": slug},
            )
            session.add(projet)
            await session.flush()
            await ensure_defaults(session, projet)
            session.add(WorkItem(project_id=projet.id, tracker_key=f"{slug}-1", title="T", state="ready"))
            ids[slug] = projet.id
    return ids


async def test_une_session_sans_organisation_ne_voit_rien(deux_organisations: dict[str, str]) -> None:
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:  # aucune portée : c'est l'oubli qui ouvrait tout
        assert (await session.execute(select(Project))).scalars().all() == []
        assert (await session.execute(select(WorkItem))).scalars().all() == []
    async with session_scope(orgs=["a"]) as session:
        projets = (await session.execute(select(Project))).scalars().all()
        assert [p.id for p in projets] == [deux_organisations["a"]]
        tickets = (await session.execute(select(WorkItem.tracker_key))).scalars().all()
        assert tickets == ["a-1"]
        # même par identifiant : la ligne de l'autre organisation n'existe pas pour cette session
        assert await session.get(Project, deux_organisations["b"]) is None
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(Project))).scalars().all()) == 2


async def test_par_l_api_un_membre_de_a_ne_voit_pas_b(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    """Le principal borne la session : le projet de `b` n'existe pas pour un membre de `a` (404, pas 403)."""
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "dev@a.test")  # développeur de `a` seulement (OIDC_DEFAULT_ORG=a)
        me = (await client.get("/api/v1/me")).json()
        assert {m["org"] for m in me["memberships"]} == {"a"}

        assert (await client.get(f"/api/v1/projects/{deux_organisations['a']}")).status_code == 200
        assert (await client.get(f"/api/v1/projects/{deux_organisations['b']}")).status_code == 404
        assert (await client.get("/api/v1/projects/b:billing-api")).status_code == 404
        liste = (await client.get("/api/v1/orgs/a/projects")).json()
        assert [p["slug"] for p in liste["items"]] == ["billing-api"]


async def test_un_administrateur_cree_un_projet_dans_son_organisation(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    """L'INSERT passe la politique (jugée sur `org_id`, pas sur un sous-select qui ne voit pas la ligne)."""
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")
        created = await client.post(
            "/api/v1/orgs/a/projects",
            json={"slug": "nouveau", "name": "Nouveau", "config": {"slug": "nouveau", "org": "a"}},
        )
        assert created.status_code == 201, created.text
        assert (
            await client.post(
                "/api/v1/orgs/b/projects",
                json={"slug": "intrus", "name": "Intrus", "config": {"slug": "intrus", "org": "b"}},
            )
        ).status_code == 403


async def test_un_jeton_de_run_voit_son_run_quelle_que_soit_l_organisation(
    pg_app: Any, deux_organisations: dict[str, str]
) -> None:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope(orgs="*") as session:
        item = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == "b-1"))).scalar_one()
        run = Run(
            id="b-1-t-implement-1",
            work_item_id=item.id,
            project_id=deux_organisations["b"],
            transition_id="t-implement",
            stage_role="implement",
            attempt=1,
            actor="agent",
            backend="claude-code",
            model="platform/standard",
            status="running",
            stage_input={"schema": "choregos/StageInput/v1"},
        )
        session.add(run)
    jeton = mint_run_token(
        "b-1-t-implement-1", project_slug="billing-api", work_item_key="b-1", ttl_minutes=5
    )
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        reponse = await client.get(
            "/api/v1/internal/runs/b-1-t-implement-1/ticket", headers={"Authorization": f"Bearer {jeton}"}
        )
        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["key"] == "b-1"
