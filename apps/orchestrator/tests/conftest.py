"""Fixtures de l'orchestrateur : base éphémère, projet de démonstration, worker Temporal."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

os.environ.setdefault("CHOREGOS_ENV", "test")
os.environ.setdefault("CHOREGOS_FAKES", "1")


@dataclass
class Fixture:
    """Le décor d'un test : un projet, un ticket, les fakes partagés."""

    project_id: str
    project_slug: str
    work_item_id: str
    tracker_key: str
    adapters: Any


@pytest.fixture
async def setup(tmp_path: Any) -> AsyncIterator[Fixture]:
    os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/orch.db"
    from choregos_adapters import AdapterSet
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.models import Organization, Project, WorkItem
    from choregos_api.db.session import create_all, session_scope
    from choregos_api.services import ensure_defaults
    from choregos_orchestrator.activities.base import set_adapters_override
    from choregos_orchestrator.config import reset_settings_cache as reset_orchestrator_cache
    from choregos_orchestrator.train_client import clear_fake_signals

    reset_settings_cache()
    reset_orchestrator_cache()
    await db_session.dispose_engine()
    await create_all()
    clear_fake_signals()

    adapters = AdapterSet.fakes()
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
        item = WorkItem(
            project_id=project.id,
            tracker_key="varga/billing-api#123",
            title="Les avoirs ne sont pas déduits du total",
            body_snapshot="Quand une commande a un avoir, le total affiché ignore la remise.",
            state="inbox",
            size="M",
            risk="low",
            allowed_paths=["src/orders/**", "tests/orders/**"],
        )
        session.add(item)
        await session.flush()
        fixture = Fixture(
            project_id=project.id,
            project_slug=project.slug,
            work_item_id=item.id,
            tracker_key=item.tracker_key,
            adapters=adapters,
        )

    # le tracker fake connaît le même ticket, pour que le miroir d'état fonctionne
    adapters.tracker.items[fixture.tracker_key] = _fake_item(fixture.tracker_key, item.title)
    adapters.scm.set_diff(
        "varga/billing-api",
        "main",
        "choregos/123",
        [("src/orders/total.py", 20, 4), ("tests/orders/test_total.py", 30, 0)],
    )

    yield fixture

    set_adapters_override(None)
    await db_session.dispose_engine()
    reset_settings_cache()


def _fake_item(key: str, title: str) -> Any:
    from choregos_core.domain import WorkItemData

    return WorkItemData(key=key, title=title, state="Todo")


@pytest.fixture
async def temporal_env() -> AsyncIterator[Any]:
    """Serveur Temporal de test avec saut dans le temps (les timers sont instantanés)."""
    from temporalio.testing import WorkflowEnvironment

    env = await WorkflowEnvironment.start_time_skipping()
    try:
        yield env
    finally:
        await env.shutdown()


@pytest.fixture
def worker_factory(temporal_env: Any) -> Any:
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES
    from temporalio.worker import Worker

    def factory(task_queue: str = "test") -> Worker:
        return Worker(
            temporal_env.client,
            task_queue=task_queue,
            workflows=ALL_WORKFLOWS,
            activities=[*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES],
        )

    return factory


def stage_result(summary: str = "fait", **kwargs: Any) -> Any:
    """Résultat d'étape crédible : preuves complètes, périmètre respecté."""
    from choregos_contracts import Evidence, StageResult, StageStatus

    payload: dict[str, Any] = {
        "status": StageStatus.DONE,
        "summary": summary,
        "evidence": Evidence(
            tests_passed=True,
            tests_run=412,
            tests_failed=0,
            coverage_delta=1.2,
            lint="ok",
            typecheck="ok",
            security_scan="ok",
        ),
    }
    payload.update(kwargs)
    return StageResult(**payload)
