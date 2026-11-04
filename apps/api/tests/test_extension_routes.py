# SPDX-License-Identifier: Apache-2.0
"""Can an INSTALLED extension serve its own routes, and only ADD?

The extension is a real module with its `.dist-info` on disk, found by `importlib.metadata` as at
start-up: it is `create_app()` that loads it and includes its router, not the test.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

EXTENSION = '''
from fastapi import APIRouter

from choregos_api.deps import Me
from choregos_api.extensions import declare_router

router = APIRouter(tags=["extension"])


@router.get("/extension/ping")
async def ping() -> dict[str, str]:
    return {"pong": "extension"}


@router.get("/extension/me")
async def me(principal: Me) -> dict[str, str]:
    """The core's dependencies serve an extension's routes like its own."""
    return {"email": principal.email}


def attach():
    declare_router(router)
'''

#: Overlaps the core's `GET /api/v1/orgs`.
OVERLAPPING_EXTENSION = """
from fastapi import APIRouter

from choregos_api.extensions import declare_router

router = APIRouter()


@router.get("/orgs")
async def all_the_orgs() -> list[str]:
    return ["everybody"]


def attach():
    declare_router(router)
"""


def _install(root: pathlib.Path, name: str, source: str) -> None:
    """Writes a module and the `.dist-info` that declares it, as `pip install` would."""
    (root / f"{name}.py").write_text(source, encoding="utf-8")
    info = root / f"{name}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[choregos.plugins]\n{name} = {name}:attach\n", encoding="utf-8")


def _extension(tmp_path: pathlib.Path, name: str, source: str) -> Iterator[None]:
    from choregos_api.extensions import reset

    root = tmp_path / name
    root.mkdir()
    _install(root, name, source)
    sys.path.insert(0, str(root))
    try:
        yield
    finally:
        sys.path.remove(str(root))
        sys.modules.pop(name, None)
        reset()


@pytest.fixture
def extension_with_routes(tmp_path: pathlib.Path) -> Iterator[None]:
    yield from _extension(tmp_path, "extension_with_routes", EXTENSION)


@pytest.fixture
def overlapping_extension(tmp_path: pathlib.Path) -> Iterator[None]:
    yield from _extension(tmp_path, "overlapping_extension", OVERLAPPING_EXTENSION)


@pytest.fixture
async def client_with_extension(
    extension_with_routes: None, tmp_path: pathlib.Path
) -> AsyncIterator[AsyncClient]:
    """The application built AFTER the extension is installed, as when a pod starts."""
    import os

    os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/extension.db"
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.session import create_all
    from choregos_api.main import create_app

    reset_settings_cache()
    await db_session.dispose_engine()
    application = create_app()
    await create_all()
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as http:
        yield http
    await db_session.dispose_engine()
    reset_settings_cache()


async def test_the_extension_serves_its_route(client_with_extension: AsyncClient) -> None:
    response = await client_with_extension.get("/api/v1/extension/ping")
    assert response.status_code == 200, response.text
    assert response.json() == {"pong": "extension"}


async def test_the_extension_route_is_guarded_by_the_cores_authentication(
    client_with_extension: AsyncClient,
) -> None:
    anonymous = await client_with_extension.get("/api/v1/extension/me")
    assert anonymous.status_code == 401, anonymous.text

    start = await client_with_extension.get("/api/v1/auth/login", params={"as": "admin@varga.dev"})
    await client_with_extension.get(start.headers["location"])
    signed_in = await client_with_extension.get("/api/v1/extension/me")
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json() == {"email": "admin@varga.dev"}


async def test_the_extension_route_appears_in_the_openapi(client_with_extension: AsyncClient) -> None:
    """An operator must be able to see what an extension adds to the API surface."""
    paths = (await client_with_extension.get("/openapi.json")).json()["paths"]
    assert "/api/v1/extension/ping" in paths


def test_an_extension_that_overlaps_a_core_route_stops_the_start(overlapping_extension: None) -> None:
    from choregos_api.main import create_app

    with pytest.raises(RuntimeError, match=r"déjà servies : GET /api/v1/orgs"):  # lang: fr-ok
        create_app()


def test_without_an_extension_no_route_is_added() -> None:
    """The control: the route does not exist by itself in the core.

    Read in the OpenAPI and not in `app.routes`, which no longer shows the included routes since
    FastAPI 0.141: that control would have been green whatever happened.
    """
    from choregos_api.main import create_app

    paths: Any = create_app().openapi()["paths"]
    assert "/api/v1/orgs" in paths, "the control would see nothing: the OpenAPI is empty"
    assert "/api/v1/extension/ping" not in paths
