"""Fixtures de l'API : base éphémère, application, identités, projet de test."""

from __future__ import annotations

import asyncio
import os
import pathlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("CHOREGOS_ENV", "test")
os.environ.setdefault("CHOREGOS_FAKES", "1")
os.environ.setdefault("CHOREGOS_DEV_LOGIN_ENABLED", "true")
os.environ.setdefault("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")


#: `CHOREGOS_TEST_SUITE_SUR_POSTGRES=1` (avec `CHOREGOS_TEST_DATABASE_URL`) : TOUTE la suite de l'API
#: tourne sur PostgreSQL, schéma migré, rôle non superutilisateur — donc sous RLS. Sur SQLite la RLS
#: n'existe pas : les webhooks n'acheminaient rien en production sans qu'un seul test le voie (#143).
SUITE_SUR_POSTGRES = os.environ.get("CHOREGOS_TEST_SUITE_SUR_POSTGRES") == "1"


@pytest.fixture
async def app(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.main import create_app
    from choregos_api.temporal import FakeTemporal, set_temporal

    if SUITE_SUR_POSTGRES:
        await _preparer_postgres()
        # Dans les TESTS, `session_scope()` sans portée est un raccourci pour « en tant que la
        # plateforme » (poser un décor). Le code produit ne l'appelle jamais ainsi — vérifié par
        # `grep` — : le traduire ici ne masque aucun défaut du produit, et `test_rls_postgres.py`
        # (fixture `pg_app`) garde le vrai comportement fail-closed.
        origine = db_session.session_scope

        def en_plateforme(org_slug: str | None = None, *, orgs: Any = None) -> Any:
            if org_slug is None and orgs is None:
                return origine(orgs="*")
            return origine(org_slug, orgs=orgs)

        monkeypatch.setattr(db_session, "session_scope", en_plateforme)
    else:
        os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    reset_settings_cache()
    await db_session.dispose_engine()
    if SUITE_SUR_POSTGRES:
        await _migrer()
    set_temporal(FakeTemporal())
    application = create_app()
    if not SUITE_SUR_POSTGRES:
        from choregos_api.db.session import create_all

        await create_all()
    yield application
    await db_session.dispose_engine()
    set_temporal(None)
    if SUITE_SUR_POSTGRES:
        await _administrer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
    reset_settings_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
async def org(app: Any) -> str:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(Organization(slug="varga", name="Varga Foundation"))
    return "varga"


async def login(client: AsyncClient, email: str) -> None:
    """Connexion de développement : `?as=email` ouvre une session sans IdP."""
    start = await client.get("/api/v1/auth/login", params={"as": email})
    assert start.status_code == 307, start.text
    callback = await client.get(start.headers["location"])
    assert callback.status_code == 307, callback.text
    assert client.cookies.get("choregos_session"), "aucun cookie de session posé"


@pytest.fixture
async def admin(client: AsyncClient, org: str) -> str:
    await login(client, "admin@varga.dev")
    return "admin@varga.dev"


@pytest.fixture
async def project(client: AsyncClient, admin: str) -> dict[str, Any]:
    payload = {
        "slug": "billing-api",
        "name": "Billing API",
        "config": {
            "slug": "billing-api",
            "org": "varga",
            "repo": {
                "url": "https://github.com/varga/billing-api.git",
                "default_branch": "main",
                "language": "python",
                "test_command": "make test",
            },
        },
    }
    response = await client.post("/api/v1/orgs/varga/projects", json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


# ───────────────────────── un vrai PostgreSQL, quand il y en a un ─────────────────────────

PG_URL = os.environ.get("CHOREGOS_TEST_DATABASE_URL", "")
sans_postgres = pytest.mark.skipif(
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


async def _preparer_postgres() -> None:
    """Schéma vide, rôle applicatif NON superutilisateur, et l'URL qui le désigne."""
    await _administrer(
        PG_URL,
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER; END IF; END $$",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_ROLE}",
    )
    os.environ["CHOREGOS_DATABASE_URL"] = _url_app(PG_URL)


async def _migrer() -> None:
    """Le schéma par `python -m choregos_api.migrer`, la commande du Job du chart."""
    from choregos_api.migrer import configuration

    await asyncio.to_thread(command.upgrade, configuration(), "heads")


@pytest.fixture
async def pg_app(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """L'application sur un PostgreSQL migré par Alembic (et non `create_all`), en rôle
    applicatif non superutilisateur, vidé à la fin."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.main import create_app
    from choregos_api.temporal import FakeTemporal, set_temporal

    await _preparer_postgres()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _url_app(PG_URL))
    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", "a")
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@a.test")
    reset_settings_cache()
    await db_session.dispose_engine()
    # La configuration de `python -m choregos_api.migrer`, celle du Job du chart : ce banc
    # PostgreSQL éprouve donc la commande qu'on déploie, pas une variante de test.
    from choregos_api.migrer import configuration

    config = configuration()
    # `migrations/env.py` fait `asyncio.run()` : interdit depuis une boucle déjà en cours,
    # donc dans un fil à part. Les tables appartiennent au rôle applicatif : `FORCE ROW
    # LEVEL SECURITY` s'applique donc à lui aussi.
    await asyncio.to_thread(command.upgrade, config, "heads")
    set_temporal(FakeTemporal())
    try:
        yield create_app()
    finally:
        await db_session.dispose_engine()
        await _administrer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        set_temporal(None)
        reset_settings_cache()
