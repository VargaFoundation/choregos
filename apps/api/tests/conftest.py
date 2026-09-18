"""Fixtures de l'API : base éphémère, application, identités, projet de test."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("CHOREGOS_ENV", "test")
os.environ.setdefault("CHOREGOS_FAKES", "1")
os.environ.setdefault("CHOREGOS_DEV_LOGIN_ENABLED", "true")


@pytest.fixture
async def app(tmp_path: Any) -> AsyncIterator[Any]:
    os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.main import create_app
    from choregos_api.temporal import FakeTemporal, set_temporal

    reset_settings_cache()
    await db_session.dispose_engine()
    set_temporal(FakeTemporal())
    application = create_app()
    from choregos_api.db.session import create_all

    await create_all()
    yield application
    await db_session.dispose_engine()
    set_temporal(None)
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
