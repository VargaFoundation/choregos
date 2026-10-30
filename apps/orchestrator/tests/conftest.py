"""Orchestrator fixtures: an ephemeral database, a demo project, a Temporal worker."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from . import pg_kit

os.environ.setdefault("CHOREGOS_ENV", "test")
os.environ.setdefault("CHOREGOS_FAKES", "1")


def recording_history() -> str | None:
    """The value of the history-recording switch (`CHOREGOS_RECORD_HISTORY`), or None when off.

    The English variable is read first; the former French name stays a working alias."""
    return os.environ.get("CHOREGOS_RECORD_HISTORY") or os.environ.get(
        "CHOREGOS_ARCHIVER_HISTORIQUE"  # lang: fr-ok alias:internal
    )


@dataclass
class Fixture:
    """A test's scene: a project, a work item, the shared fakes."""

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
            title="Les avoirs ne sont pas déduits du total",  # lang: fr-ok (asserted verbatim)
            body_snapshot="Quand une commande a un avoir, le total affiché ignore la remise.",  # lang: fr-ok
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

    # the fake tracker knows the same work item, so the state mirror works
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
    """A test Temporal server with time skipping (timers are instant)."""
    from temporalio.testing import WorkflowEnvironment

    env = await WorkflowEnvironment.start_time_skipping()
    try:
        yield env
    finally:
        await env.shutdown()


@pytest.fixture
def worker_factory(temporal_env: Any) -> Any:
    """The test's workers, **one per queue**, as the chart deploys one per queue.

    A single worker carrying every activity proves nothing about the split: it answers whatever
    queue is asked. The workflows schedule each activity on its declared queue
    (`workflows.planification`), and that is exactly what must be exercised. On 2026-09-27, on the
    dev tenant, `start_run` ran on `orchestrator` while the network policy opened the Kubernetes API
    to `executor` only: the Job was never created.
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


def stage_result(summary: str = "done", **kwargs: Any) -> Any:
    """A credible stage result: complete evidence, scope respected."""
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
