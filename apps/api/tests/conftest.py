"""API fixtures: ephemeral database, application, identities, test project."""

from __future__ import annotations

import asyncio
import contextlib
import os
import pathlib
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

# Shared fixtures of the kits: every test module of the directory can use them by name.
from .kits.identity_seams import extended_client, identity_extension  # noqa: F401

os.environ.setdefault("CHOREGOS_ENV", "test")
os.environ.setdefault("CHOREGOS_FAKES", "1")
# The egress allowlist (S39-22) only warns in the suites: the tests that prove it ask for `enforce`.
os.environ.setdefault("CHOREGOS_EGRESS_MODE", "warn")
# The registry the test plugins' servers come from (ADR 0096): the tests that prove the allowlist set another.
os.environ.setdefault("CHOREGOS_PLUGIN_SERVERS_REGISTRIES", '["ghcr.io/vargafoundation/choregos-agents"]')
os.environ.setdefault("CHOREGOS_DEV_LOGIN_ENABLED", "true")
os.environ.setdefault("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
# The suites build prod and https apps, which refuse a short or default session secret at start.
os.environ.setdefault("CHOREGOS_SESSION_SECRET", "test-session-secret-" + "x" * 32)
# A run's write is a governed action that Temporal plays (#241); here `FakeTemporal` does not play
# it: the broker does not wait thirty seconds for an outcome that will never come.
os.environ.setdefault("CHOREGOS_COURTIER_ATTENTE_ECRITURE_S", "0.3")


#: `CHOREGOS_TEST_SUITE_ON_POSTGRES=1` (with `CHOREGOS_TEST_DATABASE_URL`): the WHOLE API suite runs on
#: PostgreSQL, migrated schema, non-superuser role, so under RLS. On SQLite there is no RLS: webhooks
#: delivered nothing in production without a single test seeing it (#143).
SUITE_ON_POSTGRES = bool(os.environ.get("CHOREGOS_TEST_SUITE_ON_POSTGRES"))


@pytest.fixture
async def app(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.main import create_app
    from choregos_api.temporal import FakeTemporal, set_temporal

    if SUITE_ON_POSTGRES:
        await _prepare_postgres()
        # In the TESTS, `session_scope()` without a scope is a shortcut for "as the platform"
        # (setting the scene). The product code never calls it that way (checked with `grep`), so
        # translating it here hides no product defect, and `test_rls_postgres.py` (fixture
        # `pg_app`) keeps the real fail-closed behaviour.
        original = db_session.session_scope

        def as_platform(org_slug: str | None = None, *, orgs: Any = None) -> Any:
            if org_slug is None and orgs is None:
                return original(orgs="*")
            return original(org_slug, orgs=orgs)

        monkeypatch.setattr(db_session, "session_scope", as_platform)
    else:
        os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    reset_settings_cache()
    await db_session.dispose_engine()
    if SUITE_ON_POSTGRES:
        await _migrate()
    set_temporal(FakeTemporal())
    application = create_app()
    if not SUITE_ON_POSTGRES:
        from choregos_api.db.session import create_all

        await create_all()
    yield application
    await db_session.dispose_engine()
    set_temporal(None)
    if SUITE_ON_POSTGRES:
        await _administer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
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
    """Development sign-in: `?as=email` opens a session without an IdP."""
    start = await client.get("/api/v1/auth/login", params={"as": email})
    assert start.status_code == 307, start.text
    callback = await client.get(start.headers["location"])
    assert callback.status_code == 307, callback.text
    assert client.cookies.get("choregos_session"), "no session cookie was set"


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


# ───────────────────────── a real PostgreSQL, when there is one ─────────────────────────

PG_URL = os.environ.get("CHOREGOS_TEST_DATABASE_URL", "")
without_postgres = pytest.mark.skipif(
    not PG_URL.startswith("postgresql"), reason="CHOREGOS_TEST_DATABASE_URL absent: no PostgreSQL"
)
API = pathlib.Path(__file__).resolve().parents[1]
APP_ROLE, APP_PASSWORD = "choregos_app", "app"


def _app_url(url: str) -> str:
    """The same database, seen by the NON-superuser application role.

    A superuser ignores RLS, whatever the policies say. The role of the test container (and of
    the CI service) is one: testing with it would prove that RLS does nothing, and that would be
    true. It is also what the deployment must guarantee: the API never connects as a superuser.
    """
    scheme, rest = url.split("://", 1)
    _, host = rest.rsplit("@", 1)
    return f"{scheme}://{APP_ROLE}:{APP_PASSWORD}@{host}"


async def _administer(url: str, *statements: str) -> None:
    """Run statements as a superuser, outside any SQLAlchemy engine."""
    import asyncpg

    connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        for statement in statements:
            await connection.execute(statement)
    finally:
        await connection.close()


async def _prepare_postgres() -> None:
    """Empty schema, NON-superuser application role, and the URL that names it."""
    await _administer(
        PG_URL,
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
        f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER; END IF; END $$",
        f"GRANT USAGE, CREATE ON SCHEMA public TO {APP_ROLE}",
    )
    os.environ["CHOREGOS_DATABASE_URL"] = _app_url(PG_URL)
    # pgvector is not a trusted extension: the cluster's bootstrap creates it as a superuser
    # (`postInitApplicationSQL`, ADR 0051), never the application role. Done the same way here, when
    # the image carries it; on an image without it, the knowledge index serves full-text only.
    # `postgres:16-alpine` has no pgvector, and that is a case under test too.
    with contextlib.suppress(Exception):
        await _administer(PG_URL, "CREATE EXTENSION IF NOT EXISTS vector")


async def _migrate() -> None:
    """The schema through `python -m choregos_api.migrate`, the command of the chart's Job."""
    from choregos_api.migrate import configuration

    await asyncio.to_thread(command.upgrade, configuration(), "heads")


@pytest.fixture
async def pg_app(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """The application on a PostgreSQL migrated by Alembic (not `create_all`), as the
    non-superuser application role, emptied at the end."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.main import create_app
    from choregos_api.temporal import FakeTemporal, set_temporal

    await _prepare_postgres()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", _app_url(PG_URL))
    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", "a")
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@a.test")
    reset_settings_cache()
    await db_session.dispose_engine()
    # The configuration of `python -m choregos_api.migrate`, the chart Job's: this PostgreSQL bench
    # therefore proves the command that is deployed, not a test variant.
    from choregos_api.migrate import configuration

    config = configuration()
    # `migrations/env.py` calls `asyncio.run()`: forbidden from a running loop, hence a separate
    # thread. The tables belong to the application role: `FORCE ROW LEVEL SECURITY` therefore
    # applies to it too.
    await asyncio.to_thread(command.upgrade, config, "heads")
    set_temporal(FakeTemporal())
    try:
        yield create_app()
    finally:
        await db_session.dispose_engine()
        await _administer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        set_temporal(None)
        reset_settings_cache()


# ───────────────────────── a GitHub App, scriptable (S40-04, ADR 0073) ─────────────────────────


@pytest.fixture
def github() -> Iterator[Any]:
    """A deployment that HAS a GitHub App, scriptable; reset around the test."""
    from choregos_adapters.fakes.github_app import FakeGitHubApp

    fake = FakeGitHubApp.shared()
    fake.reset()
    fake.configured = True
    yield fake
    fake.reset()
