"""Setting for the e2e scenarios: full in-memory platform, test Temporal server.

These scenarios run **without a cluster**: they exercise the API, the orchestrator, the gates,
the train and the findings, with simulated connectors. The variants on kind (real
provisioning, Tekton, Argo) are marked `integration` and run nightly.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

#: The end-to-end bench environment. It is NO LONGER set at import (#259): a session that
#: collected `tests/e2e` with another directory had these variables imposed on it — an API test
#: that assumed an empty webhook secret got 401. Only the `environnement_e2e` fixture sets them,
#: for the duration of this package's tests, and gives the environment back as it found it.
ENVIRONNEMENT_E2E: dict[str, str] = {
    "CHOREGOS_FAKES": "1",
    "CHOREGOS_ENV": "test",
    # The development login is OFF by default since 2026-09-24: a test that uses it
    # must say so, and name who is admin.
    "CHOREGOS_DEV_LOGIN_ENABLED": "true",
    "CHOREGOS_DEV_ADMIN_EMAILS": "admin@varga.dev",
    # Webhooks refuse an empty secret since P0-3: the end-to-end bench sets one.
    "CHOREGOS_GENERIC_WEBHOOK_SECRET": "e2e-webhook-secret",
}


@contextlib.contextmanager
def environnement_pose(variables: dict[str, str]) -> Iterator[None]:
    """Set `variables` where the environment says nothing (a value already set wins, like
    `setdefault`), then give each variable back as it was — absent becomes absent again.

    The API settings are cached: the cache is cleared on entry and on exit, so that neither this
    package nor the next reads settings computed under the other environment.
    """
    from choregos_api.config import reset_settings_cache

    avant = {nom: os.environ.get(nom) for nom in variables}
    for nom, valeur in variables.items():
        os.environ.setdefault(nom, valeur)
    reset_settings_cache()
    try:
        yield
    finally:
        for nom, valeur in avant.items():
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur
        reset_settings_cache()


@pytest.fixture(scope="package", autouse=True)
def environnement_e2e() -> Iterator[None]:
    """The bench environment, for the duration of the `tests/e2e` tests and no longer.

    `package` scope rather than `session`: a session fixture is only torn down at the end of the
    session, hence AFTER the tests of another directory collected in the same session; this one
    is torn down at the package's last test.
    """
    with environnement_pose(ENVIRONNEMENT_E2E):
        yield


class BridgedTemporal:
    """The API's Temporal gateway wired to the test server.

    The e2e scenarios must exercise the **real** path: the API queries and signals the
    workflows actually running. Calls to an absent workflow are tolerated (the scenario did
    not start it), but never silently invented.
    """

    def __init__(self, client: Any) -> None:
        self.client = client
        self.missing: list[tuple[str, str]] = []

    async def start_interpreter(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("WorkflowInterpreter", workflow_id, payload)

    async def start_train(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("ReleaseTrain", workflow_id, payload)

    async def start_provisioning(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("ProjectProvisioning", workflow_id, payload)

    async def start_action(self, workflow_id: str, payload: dict[str, Any]) -> str:
        """An approved action REALLY starts (ADR 0035): it is the API that starts it when a
        person decides, not the scenario — on the bench workers' queue."""
        import contextlib

        from temporalio.exceptions import WorkflowAlreadyStartedError

        with contextlib.suppress(WorkflowAlreadyStartedError):
            await self.client.start_workflow("ActionWorkflow", payload, id=workflow_id, task_queue="e2e")
        return workflow_id

    async def describe(self, workflow_id: str) -> Any:
        return None

    async def _start(self, workflow: str, workflow_id: str, payload: dict[str, Any]) -> str:
        # The scenarios start their own workflows on their task queue; here we merely
        # note the intent, as a worker not yet started would.
        self.missing.append(("start", workflow_id))
        return workflow_id

    async def signal(self, workflow_id: str, name: str, payload: Any) -> None:
        try:
            await self.client.get_workflow_handle(workflow_id).signal(name, payload)
        except Exception:
            self.missing.append((name, workflow_id))

    async def query(self, workflow_id: str, name: str) -> Any:
        try:
            return await self.client.get_workflow_handle(workflow_id).query(name)
        except Exception:
            return None

    async def cancel(self, workflow_id: str) -> None:
        try:
            await self.client.get_workflow_handle(workflow_id).cancel()
        except Exception:
            self.missing.append(("cancel", workflow_id))


@dataclass
class Platform:
    """Everything a scenario handles: the API, the database, the adapters, Temporal."""

    client: Any
    project_id: str
    project_slug: str
    adapters: Any
    env: Any

    async def work_item(self, key: str) -> Any:
        from choregos_api.db.models import WorkItem
        from choregos_api.db.session import session_scope
        from sqlalchemy import select

        async with session_scope() as session:
            return (
                await session.execute(select(WorkItem).where(WorkItem.tracker_key == key))
            ).scalar_one_or_none()


@pytest.fixture
async def platform(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Platform]:
    # Through `monkeypatch`: a scenario's database does not outlive its test (#259).
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/e2e.db")
    from choregos_adapters import AdapterSet
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import create_all, session_scope
    from choregos_api.main import create_app
    from choregos_api.services import ensure_defaults
    from choregos_api.temporal import FakeTemporal, set_temporal
    from choregos_orchestrator.activities.base import set_adapters_override
    from choregos_orchestrator.train_client import clear_fake_signals
    from httpx import ASGITransport, AsyncClient
    from temporalio.testing import WorkflowEnvironment

    reset_settings_cache()
    await db_session.dispose_engine()
    await create_all()
    clear_fake_signals()
    set_temporal(FakeTemporal())

    adapters = AdapterSet.fakes()
    adapters.gateway.auto_usage = True
    adapters.tracker.project_slug = "varga/billing-api"
    set_adapters_override(adapters)

    async with session_scope() as session:
        org = Organization(slug="varga", name="Varga Foundation")
        session.add(org)
        await session.flush()
        project = Project(
            org_id=org.id,
            slug="billing-api",
            name="Billing API",
            status="active",
            config={
                "slug": "billing-api",
                "org": "varga",
                "repo": {
                    "url": "https://github.com/varga/billing-api.git",
                    "default_branch": "main",
                    "language": "python",
                    "test_command": "make test",
                },
                "gitops": {"repo_url": "https://github.com/varga/gitops.git", "apps": ["billing-api"]},
                "notify": {"slack_channel": "#billing"},
            },
        )
        session.add(project)
        await session.flush()
        await ensure_defaults(session, project)
        project_id, project_slug = project.id, project.slug

    adapters.cd.set_health("billing-api", "Healthy")
    environment = await WorkflowEnvironment.start_time_skipping()
    set_temporal(BridgedTemporal(environment.client))
    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://e2e") as http:
        yield Platform(
            client=http, project_id=project_id, project_slug=project_slug, adapters=adapters, env=environment
        )
    await environment.shutdown()
    set_adapters_override(None)
    set_temporal(None)
    await db_session.dispose_engine()
    reset_settings_cache()


@pytest.fixture
def worker(platform: Platform) -> Any:
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES

    def factory(task_queue: str = "e2e") -> Any:
        return workers_repartis(
            platform.env.client,
            activites=[*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES],
            workflows=ALL_WORKFLOWS,
            file_du_workflow=task_queue,
        )

    return factory


async def login(client: Any, email: str = "admin@varga.dev") -> None:
    start = await client.get("/api/v1/auth/login", params={"as": email})
    await client.get(start.headers["location"])
