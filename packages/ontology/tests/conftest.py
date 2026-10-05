# SPDX-License-Identifier: Apache-2.0
"""Shared helpers: a copy of the reference package that a test may mutate, the plugin switched on
in the core, and the core's application built after it."""

from __future__ import annotations

import os
import shutil
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


@pytest.fixture
def greffon(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The plugin, active for the duration of the test: its entry points come from the installed
    package, and `CHOREGOS_ESSAI_ONTOLOGIE=1` switches it on — exactly as in a deployment."""
    from choregos_api.greffons import reinitialiser

    monkeypatch.setenv("CHOREGOS_ESSAI_ONTOLOGIE", "1")
    try:
        yield
    finally:
        reinitialiser()


@pytest.fixture
async def app(greffon: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
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


class PasserelleDeTest:
    """La passerelle Temporal de l'API branchée sur le serveur de test (S20-08) : une action approuvée
    part VRAIMENT dans un `ActionWorkflow`, et une preuve remise atteint le workflow qui l'attend."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.signals: list[tuple[str, str, Any]] = []

    async def start_action(self, workflow_id: str, payload: dict[str, Any]) -> str:
        import contextlib

        from temporalio.exceptions import WorkflowAlreadyStartedError

        with contextlib.suppress(WorkflowAlreadyStartedError):
            await self.client.start_workflow("ActionWorkflow", payload, id=workflow_id, task_queue="test")
        return workflow_id

    async def signal(self, workflow_id: str, name: str, payload: Any) -> None:
        self.signals.append((workflow_id, name, payload))
        await self.client.get_workflow_handle(workflow_id).signal(name, payload)

    async def start_interpreter(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return workflow_id

    async def start_train(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return workflow_id

    async def start_provisioning(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return workflow_id

    async def query(self, workflow_id: str, name: str) -> Any:
        return None

    async def cancel(self, workflow_id: str) -> None:
        return None

    async def describe(self, workflow_id: str) -> Any:
        return None


@pytest.fixture
async def temporal(app: Any) -> AsyncIterator[Any]:
    """Un serveur Temporal de test et les workers de la plateforme : l'`ActionWorkflow` joue les
    effets de l'ontologie, comme dans un déploiement."""
    from choregos_api.temporal import set_temporal
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES
    from temporalio.testing import WorkflowEnvironment

    environnement = await WorkflowEnvironment.start_time_skipping()
    set_temporal(PasserelleDeTest(environnement.client))
    try:
        async with workers_repartis(
            environnement.client,
            activites=[*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES],
            workflows=ALL_WORKFLOWS,
            file_du_workflow="test",
        ):
            yield environnement
    finally:
        await environnement.shutdown()
