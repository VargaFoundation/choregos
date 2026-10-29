"""Fixtures de l'orchestrateur : base éphémère, projet de démonstration, worker Temporal."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from . import pg_kit

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


#: `CHOREGOS_TEST_ORCH_ON_POSTGRES=1` (with `CHOREGOS_TEST_DATABASE_URL`) runs the orchestrator
#: suite's `setup` on a real PostgreSQL (S27-11: the reservation lock is an advisory lock there, which
#: SQLite cannot exercise). The schema is migrated by Alembic and the suite connects as a non-superuser
#: role, so RLS applies (S47-27).
ON_POSTGRES = os.environ.get("CHOREGOS_TEST_ORCH_ON_POSTGRES") == "1"
PG_URL = os.environ.get("CHOREGOS_TEST_DATABASE_URL", "")


@pytest.fixture
async def setup(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Fixture]:
    if ON_POSTGRES:
        await pg_kit.prepare(PG_URL)
        os.environ["CHOREGOS_DATABASE_URL"] = pg_kit.app_url(PG_URL)
    else:
        os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/orch.db"
    from choregos_adapters import AdapterSet
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.models import Organization, Project, WorkItem
    from choregos_api.services import ensure_defaults
    from choregos_orchestrator.activities.base import set_adapters_override
    from choregos_orchestrator.config import reset_settings_cache as reset_orchestrator_cache
    from choregos_orchestrator.train_client import clear_fake_signals

    reset_settings_cache()
    reset_orchestrator_cache()
    await db_session.dispose_engine()
    if ON_POSTGRES:
        await pg_kit.migrate()
        # In the TESTS, an unscoped `session_scope()` is a shortcut for "as the platform" (setting a
        # scene). Product code never calls it unscoped, as `test_postgres_mode_runs_under_rls.py`
        # proves: translating it here hides no product defect. An explicit scope is forwarded untouched.
        original = db_session.session_scope

        def as_platform(org_slug: str | None = None, *, orgs: Any = None) -> Any:
            if org_slug is None and orgs is None:
                return original(orgs="*")
            return original(org_slug, orgs=orgs)

        monkeypatch.setattr(db_session, "session_scope", as_platform)
    else:
        from choregos_api.db.session import create_all

        await create_all()
    clear_fake_signals()

    adapters = AdapterSet.fakes()
    set_adapters_override(adapters)

    async with db_session.session_scope() as session:
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
    """Les workers du test, **un par file**, comme le chart en déploie un par file.

    Un worker unique portant toutes les activités ne prouve rien de la répartition : il répond
    quelle que soit la file demandée. Or les workflows planifient chaque activité sur sa file
    déclarée (`workflows.planification`), et c'est exactement ce qui doit être éprouvé. Le
    2026-09-27, sur le locataire dev, `start_run` tournait sur `orchestrator` alors que la
    politique réseau n'ouvrait l'API Kubernetes qu'à `executor` : le Job n'était jamais créé.
    """
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES

    def factory(task_queue: str = "test", **kwargs: Any) -> Any:
        return workers_repartis(
            temporal_env.client,
            activites=[*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES],
            workflows=ALL_WORKFLOWS,
            file_du_workflow=task_queue,
            **kwargs,
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


def refine_result(summary: str = "spec written", **kwargs: Any) -> Any:
    """A `t-refine` result with the outputs the core templates declare: without them the step
    fails (S31-02)."""
    outputs = {
        "spec_markdown": f"## Spec\n\n{summary}",
        "size": "M",
        "risk": "low",
        **kwargs.pop("outputs", {}),
    }
    return stage_result(summary, outputs=outputs, **kwargs)
