"""The identity seams, proved by an INSTALLED extension: its fixtures and the helpers around them.

The extension is a real module with its `.dist-info`; `create_app()` loads it as at start-up.
"""

from __future__ import annotations

import os
import pathlib
import sys
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

EXTENSION = """
from choregos_api.edition import (
    SessionRefusee,
    declarer_un_administrateur_de_plateforme,
    declarer_une_validation_de_session,
)

#: Set by the test.
STATE = {"revoked": set(), "seen": [], "admins": set(), "outage": False}


async def not_revoked(session, user, payload):
    STATE["seen"].append(dict(payload))
    if STATE["outage"]:
        raise ConnectionError("revocation table unreachable")
    if user.email in STATE["revoked"]:
        raise SessionRefusee("session revoked by the administrator: sign in again")


def platform_administrator(session, principal):
    return principal.email in STATE["admins"]


def plug_in():
    declarer_une_validation_de_session(not_revoked)
    declarer_un_administrateur_de_plateforme(platform_administrator)
"""


@pytest.fixture
def identity_extension(tmp_path: pathlib.Path) -> Iterator[Any]:
    from choregos_api.edition import reinitialiser

    root = tmp_path / "site"
    root.mkdir()
    (root / "identity_extension.py").write_text(EXTENSION, encoding="utf-8")
    info = root / "identity_extension-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: identity_extension\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nidentity_extension = identity_extension:plug_in\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    try:
        yield root
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("identity_extension", None)
        reinitialiser()


@pytest.fixture
async def extended_client(
    identity_extension: pathlib.Path, tmp_path: pathlib.Path
) -> AsyncIterator[AsyncClient]:
    os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/identite.db"
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.models import Organization
    from choregos_api.db.session import create_all, session_scope
    from choregos_api.main import create_app

    reset_settings_cache()
    await db_session.dispose_engine()
    application = create_app()
    await create_all()
    async with session_scope() as session:
        session.add(Organization(slug="varga", name="Varga"))
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as http:
        yield http
    await db_session.dispose_engine()
    reset_settings_cache()


def state_of() -> dict[str, Any]:
    return sys.modules["identity_extension"].STATE  # type: ignore[no-any-return]


async def connect(client: AsyncClient, email: str) -> None:
    client.cookies.clear()
    start = await client.get("/api/v1/auth/login", params={"as": email})
    await client.get(start.headers["location"])
