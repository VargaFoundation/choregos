# SPDX-License-Identifier: Apache-2.0
"""Shared helpers: a copy of the reference package that a test may mutate, and the plugin installed
in the core the way `pip` would do it."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("CHOREGOS_ENV", "test")
os.environ.setdefault("CHOREGOS_FAKES", "1")
os.environ.setdefault("CHOREGOS_DEV_LOGIN_ENABLED", "true")
os.environ.setdefault("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")

HERE = Path(__file__).parent
CORE_REF = HERE / "fixtures" / "core-ref"
SNAPSHOTS = HERE / "snapshots"


@pytest.fixture
def core_ref(tmp_path: Path) -> Path:
    target = tmp_path / "core-ref"
    shutil.copytree(CORE_REF, target)
    return target


Mutate = Callable[[str, str, str], None]


@pytest.fixture
def mutate(core_ref: Path) -> Mutate:
    """Replace ``old`` by ``new`` in one file of the copy; fails loudly if ``old`` is absent."""

    def apply(file: str, old: str, new: str) -> None:
        path = core_ref / file
        text = path.read_text(encoding="utf-8")
        assert old in text, f"{old!r} not found in {file}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    return apply


ENTRY_POINTS = """[choregos.plugins]
choregos-ontology = choregos_ontology.service.plugin:brancher

[choregos.migrations]
choregos-ontology = choregos_ontology.service.plugin:MIGRATIONS
"""


def install_plugin(root: Path) -> None:
    """The `.dist-info` that `pip install` writes for a package declaring both entry points."""
    info = root / "choregos_ontology_greffon-0.12.0.dist-info"
    info.mkdir(parents=True)
    metadata = "Metadata-Version: 2.1\nName: choregos-ontology-greffon\nVersion: 0.12.0\n"
    (info / "METADATA").write_text(metadata, encoding="utf-8")
    (info / "entry_points.txt").write_text(ENTRY_POINTS, encoding="utf-8")


@pytest.fixture
def greffon(tmp_path: Path) -> Iterator[Path]:
    """The plugin, visible to `importlib.metadata` for the duration of the test."""
    from choregos_api.greffons import reinitialiser

    root = tmp_path / "site"
    install_plugin(root)
    sys.path.insert(0, str(root))
    try:
        yield root
    finally:
        sys.path.remove(str(root))
        reinitialiser()


@pytest.fixture
async def app(greffon: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """The core's application, built AFTER the plugin is installed — as a pod would start."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.session import create_all
    from choregos_api.main import create_app
    from choregos_api.temporal import FakeTemporal, set_temporal

    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/greffon.db")
    reset_settings_cache()
    await db_session.dispose_engine()
    set_temporal(FakeTemporal())
    application = create_app()
    await create_all()
    yield application
    await db_session.dispose_engine()
    set_temporal(None)
    reset_settings_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    """An admin of the `varga` organisation, logged in."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    from .aides import connecter

    async with session_scope() as session:
        session.add(Organization(slug="varga", name="Varga Foundation"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        await connecter(http, "admin@varga.dev")
        yield http


@pytest.fixture
async def projet(client: AsyncClient) -> dict[str, Any]:
    from .aides import creer_projet

    return await creer_projet(client, "infra")
