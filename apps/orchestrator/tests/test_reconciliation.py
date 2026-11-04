"""Tracker catch-up: what a lost webhook leaves behind is picked up (S3-06)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_orchestrator.activities import tracker as tracker_activities
from choregos_orchestrator.train_client import clear_fake_signals, fake_starts

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


def _seed_candidate(setup: Fixture, title: str) -> str:
    """A ticket labelled `agent-ready` on the tracker side, that no webhook announced."""
    return setup.adapters.tracker.seed(title, labels=["agent-ready"])


async def test_a_ticket_never_announced_is_created_and_started(setup: Fixture) -> None:
    clear_fake_signals()
    key = _seed_candidate(setup, "The reduced VAT rate is not applied")

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert key in result["created"], result
    assert key in result["started"], result
    assert any(key.replace("/", "_").replace("#", "-") in wf for wf in fake_starts()), fake_starts()

    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == key))).scalar_one()
        assert row.title == "The reduced VAT rate is not applied"
        assert row.temporal_wf_id, "the caught-up ticket carries its workflow identifier"


async def test_the_catch_up_is_idempotent(setup: Fixture) -> None:
    """Two passes do not create two tickets, and do not restart the workflow."""
    clear_fake_signals()
    _seed_candidate(setup, "Possible duplicate")

    first = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    second = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert first["created"] and first["started"]
    assert second["created"] == [] and second["started"] == []
    assert len(fake_starts()) == 1


async def test_an_already_known_ticket_is_not_restarted(setup: Fixture) -> None:
    """The set's ticket already exists: the catch-up must not double its workflow."""
    clear_fake_signals()
    setup.adapters.tracker.items[setup.tracker_key].labels = ["agent-ready"]

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert result["created"] == []
    assert result["started"] == [setup.tracker_key], "it had no workflow yet"

    again = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert again["started"] == []


async def test_a_finished_or_paused_ticket_is_left_alone(setup: Fixture) -> None:
    clear_fake_signals()
    setup.adapters.tracker.items[setup.tracker_key].labels = ["agent-ready"]
    finished = _seed_candidate(setup, "Already delivered")

    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == setup.tracker_key))
        ).scalar_one()
        row.paused = True

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert setup.tracker_key not in result["started"], "a paused ticket stays paused"
    assert finished in result["started"]

    async with session_scope() as session:
        row = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == finished))).scalar_one()
        row.state = "deployed_prod"
    clear_fake_signals()
    again = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert again["started"] == [], "a terminal state is not restarted"


async def test_without_a_candidate_the_catch_up_does_nothing(setup: Fixture) -> None:
    clear_fake_signals()
    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert result == {"candidates": 0, "created": [], "started": []}
    assert fake_starts() == {}


async def test_the_periodic_loop_catches_up_then_ends(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The workflow chains the passes and stops cleanly on `stop`."""
    clear_fake_signals()
    key = _seed_candidate(setup, "Caught up by the loop")
    async with worker_factory("test"):
        handle = await temporal_env.client.start_workflow(
            "TrackerReconciliation",
            {"project_slug": setup.project_slug, "interval_seconds": 60, "max_passes": 3},
            id=f"reconcile-{setup.project_slug}",
            task_queue="test",
        )
        for _ in range(100):
            status = await handle.query("status")
            if status["passes"] >= 1:
                break
        await handle.signal("stop", {})
        outcome = await handle.result()

    assert outcome["stopped"] is True
    assert outcome["passes"] >= 1
    assert any(key.replace("/", "_").replace("#", "-") in wf for wf in fake_starts())


async def test_a_ticket_held_by_the_platform_is_discovered(setup: Fixture) -> None:
    """With `tracker: internal`, there is no outside from which to list candidates.

    Shipped on 2026-09-23, this connector returned `[]`: a ticket created by the platform
    (a promoted finding, a request entered in the front end) was never discovered and stayed
    in its initial state forever. On the 2026-09-23 bench, the two tickets coming from findings
    stayed in `inbox` from start to finish.
    """
    from choregos_adapters.tracker.internal import InternalTracker
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import internal_ticket_key, project_bundle
    from sqlalchemy import select

    clear_fake_signals()
    setup.adapters.tracker = InternalTracker()

    async with session_scope() as session:
        bundle = await project_bundle(session, setup.project_slug)
        ticket_key = await internal_ticket_key(session, bundle)
        assert ticket_key.startswith("BILLING-API-"), ticket_key
        session.add(
            WorkItem(
                project_id=setup.project_id,
                tracker_key=ticket_key,
                title="Missing candidate profile base",
                state=bundle.workflow.initial_state,
            )
        )

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert ticket_key in result["started"], result

    async with session_scope() as session:
        row = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == ticket_key))).scalar_one()
        assert row.temporal_wf_id, "the platform ticket carries its workflow identifier"


async def test_the_internal_key_follows_the_project_and_does_not_repeat(setup: Fixture) -> None:
    """The returned key was the ticket TITLE: unreadable on a board, and colliding
    with `(project_id, tracker_key)` as soon as two findings looked alike."""
    from choregos_api.db.models import Connector, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import internal_ticket_key, project_bundle

    async with session_scope() as session:
        bundle = await project_bundle(session, setup.project_slug)
        first_key = await internal_ticket_key(session, bundle)
        session.add(WorkItem(project_id=setup.project_id, tracker_key=first_key, title="a", state="inbox"))
        await session.flush()
        assert await internal_ticket_key(session, bundle) != first_key

        # The prefix is configurable: a business wants `RH-7`, not `STAFFING-7`.
        session.add(
            Connector(
                project_id=setup.project_id, kind="tracker", type="internal", config={"key_prefix": "RH"}
            )
        )
        await session.flush()
        assert (await internal_ticket_key(session, bundle)).startswith("RH-")


async def test_a_failing_pass_does_not_kill_the_loop(temporal_env: Any) -> None:
    """On 10/08, on the dev tenant, three failures without a GitHub App had left `reconcile-dev`
    dead for good: no `agent-ready` issue was read any more (S22-06). A pass that fails
    is stated in the status, and the next one goes again."""
    import uuid

    from choregos_orchestrator.workflows.reconciliation import TrackerReconciliation
    from temporalio import activity
    from temporalio.worker import Worker

    calls: list[str] = []

    @activity.defn(name="reconcile_tracker")
    async def flaky_catch_up(payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(payload["project_slug"])
        if len(calls) <= 3:  # the three attempts of the first pass
            raise RuntimeError("[github] no authentication configured")
        return {"created": ["DEMO-1"], "started": ["DEMO-1"]}

    client = temporal_env.client
    async with (
        Worker(client, task_queue="tracker", activities=[flaky_catch_up]),
        Worker(client, task_queue="test", workflows=[TrackerReconciliation]),
    ):
        handle = await client.start_workflow(
            "TrackerReconciliation",
            {"project_slug": "dev", "interval_seconds": 1},
            id=f"reconcile-{uuid.uuid4()}",
            task_queue="test",
        )
        status: dict[str, Any] = {}
        for _ in range(200):
            status = await handle.query("status")
            if status["passes"] >= 2:
                break
            await temporal_env.sleep(1)
        assert status["passes"] >= 2, status
        assert status["last"] == {"created": ["DEMO-1"], "started": ["DEMO-1"]}, status
        await handle.signal("stop")
        assert (await handle.result())["stopped"] is True


async def test_the_start_restarts_a_dead_loop_but_never_a_ticket(setup: Fixture, monkeypatch: Any) -> None:
    """At the worker's start, a catch-up loop dead by failure restarts; a ticket never starts twice
    (S22-06)."""
    from choregos_orchestrator import worker

    requests: list[tuple[str, bool]] = []

    async def capture(
        name: str,
        workflow_id: str,
        payload: dict[str, Any],
        *,
        relancer_si_echoue: bool = False,  # lang: fr-ok (the pinned keyword)
    ) -> bool:
        requests.append((workflow_id, relancer_si_echoue))
        return True

    monkeypatch.setattr("choregos_orchestrator.train_client.start_workflow_once", capture)
    await worker.start_reconciliation_loops()
    assert requests and all(restarted for _, restarted in requests), requests

    import inspect

    from choregos_orchestrator.train_client import start_workflow_once

    assert (
        inspect.signature(start_workflow_once).parameters["relancer_si_echoue"].default is False
    )  # lang: fr-ok (the pinned keyword)


async def test_an_item_a_start_rule_matches_is_caught_up_without_agent_ready(setup: Fixture) -> None:
    """S56-03: a lost webhook of an item a start rule would have started is caught up by the poll."""
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    clear_fake_signals()
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        project.config = {**project.config, "start_rules": [{"item_type": "Incident", "states": ["todo"]}]}
    tracker = setup.adapters.tracker
    incident = tracker.seed("Payroll export fails")
    tracker.items[incident].item_type = "incident"
    task = tracker.seed("Rename a column")
    tracker.items[task].item_type = "Task"

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert incident in result["created"] and incident in result["started"], result
    assert task not in result["created"], result


async def _set_project(setup: Fixture, **fields: Any) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        for name, value in fields.items():
            setattr(project, name, value)


@pytest.mark.parametrize("status", ["archived", "suspended"])
async def test_an_archived_or_suspended_project_takes_nothing_on(setup: Fixture, status: str) -> None:
    """Should the loop's `stop` have missed, a pass on an archived or suspended project starts nothing."""
    clear_fake_signals()
    _seed_candidate(setup, "Waiting while the project is retired")
    await _set_project(setup, status=status)

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert result == {"skipped": status, "created": [], "started": []}
    assert fake_starts() == {}


async def test_the_catch_up_loop_of_a_project_starts_with_the_configured_interval(
    setup: Fixture, monkeypatch: Any
) -> None:
    from choregos_orchestrator.config import get_settings
    from choregos_orchestrator.train_client import start_reconciliation_loop

    clear_fake_signals()
    monkeypatch.setattr(get_settings(), "reconcile_interval_seconds", 45)

    assert await start_reconciliation_loop("billing") is True
    assert await start_reconciliation_loop("billing") is False, "one loop per project"
    assert fake_starts() == {"reconcile-billing": {"project_slug": "billing", "interval_seconds": 45}}

    monkeypatch.setattr(get_settings(), "reconcile_interval_seconds", 0)
    assert await start_reconciliation_loop("quiet") is False, "0 disables the catch-up"
    assert "reconcile-quiet" not in fake_starts()


@pytest.mark.parametrize("failed", [False, True])
async def test_a_project_activated_by_its_provisioning_is_polled_at_once(
    setup: Fixture, failed: bool
) -> None:
    """Before, only the worker's start polled the active projects: one activated later waited for a
    restart, and `candidates_jql` took nothing on (2026-11-02)."""
    from choregos_orchestrator.activities.provisioning import finish_provisioning

    clear_fake_signals()
    step = {"name": "github.ensure_labels", "status": "failed" if failed else "succeeded"}
    await _set_project(setup, status="provisioning", provision_state={"status": "running", "steps": [step]})

    await finish_provisioning({"project_id": setup.project_id})

    loop = f"reconcile-{setup.project_slug}"
    if failed:
        assert loop not in fake_starts(), "a failed provisioning leaves a draft: nothing to poll"
    else:
        assert fake_starts()[loop]["project_slug"] == setup.project_slug


async def test_a_caught_up_issue_keeps_who_filed_it(setup: Fixture) -> None:
    """The tracker's reporter is recorded at birth (#1179): the agent checks the requester on it."""
    clear_fake_signals()
    key = setup.adapters.tracker.seed(
        "Access to billing", labels=["agent-ready"], author="Jean Martin <jmartin@varga.dev>"
    )

    await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == key))).scalar_one()
        assert row.reporter == "Jean Martin <jmartin@varga.dev>"
