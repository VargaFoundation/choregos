"""Décor des scénarios e2e : plateforme complète en mémoire, serveur Temporal de test.

Ces scénarios tournent **sans cluster** : ils exercent l'API, l'orchestrateur, les gates,
le train et les findings, avec les connecteurs simulés. Les variantes sur kind (provisioning
réel, Tekton, Argo) sont marquées `integration` et tournent la nuit.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

os.environ.setdefault("CHOREGOS_FAKES", "1")
os.environ.setdefault("CHOREGOS_ENV", "test")
# La connexion de développement est ÉTEINTE par défaut depuis le 2026-09-24 : un test
# qui s'en sert doit le dire, et nommer qui est admin.
os.environ.setdefault("CHOREGOS_DEV_LOGIN_ENABLED", "true")
os.environ.setdefault("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
# Les webhooks refusent un secret vide depuis P0-3 : le banc de bout en bout en pose un.
os.environ.setdefault("CHOREGOS_GENERIC_WEBHOOK_SECRET", "e2e-webhook-secret")


class BridgedTemporal:
    """Passerelle Temporal de l'API branchée sur le serveur de test.

    Les scénarios e2e doivent exercer le **vrai** chemin : l'API interroge et signale les
    workflows réellement en cours. Les appels vers un workflow absent sont tolérés (le
    scénario ne l'a pas démarré), mais jamais silencieusement inventés.
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

    async def _start(self, workflow: str, workflow_id: str, payload: dict[str, Any]) -> str:
        # Les scénarios démarrent eux-mêmes leurs workflows sur leur task queue ; ici on
        # se contente de noter l'intention, comme le ferait un worker non encore démarré.
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
    """Tout ce qu'un scénario manipule : l'API, la base, les adaptateurs, Temporal."""

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
async def platform(tmp_path: Any) -> AsyncIterator[Platform]:
    os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/e2e.db"
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
    from choregos_orchestrator.workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES
    from temporalio.worker import Worker

    def factory(task_queue: str = "e2e") -> Worker:
        return Worker(
            platform.env.client,
            task_queue=task_queue,
            workflows=ALL_WORKFLOWS,
            activities=[*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES],
        )

    return factory


async def login(client: Any, email: str = "admin@varga.dev") -> None:
    start = await client.get("/api/v1/auth/login", params={"as": email})
    await client.get(start.headers["location"])
