"""`FindingsTriage` et `ProjectProvisioning` : dédup, ticket lié, reprise après échec."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def _add_finding(setup: Fixture, title: str, evidence: str, **kwargs: Any) -> str:
    from choregos_api.db.models import Finding
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = Finding(
            project_id=setup.project_id,
            origin_work_item_id=setup.work_item_id,
            title=title,
            type=kwargs.get("type", "perf"),
            severity=kwargs.get("severity", "medium"),
            evidence=evidence,
            suggested_fix=kwargs.get("suggested_fix"),
            estimate=kwargs.get("estimate", "S"),
            status="pending",
        )
        session.add(row)
        await session.flush()
        return row.id


async def _wait(handle: Any, predicate: Any, timeout: float = 30.0) -> dict[str, Any]:
    for _ in range(int(timeout * 10)):
        status = await handle.query("status")
        if predicate(status):
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"condition jamais atteinte : {await handle.query('status')}")


async def test_finding_creates_linked_ticket(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    finding_id = await _add_finding(
        setup, "Requête N+1 sur les lignes", "src/orders/repository.py:88 — 1 + N requêtes"
    )
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "FindingsTriage",
            {"project_slug": setup.project_slug},
            id=f"findings-{setup.project_slug}",
            task_queue="test",
        )
        await handle.signal("finding", {"finding_id": finding_id, "project_id": setup.project_id})
        status = await _wait(handle, lambda s: s["processed"] >= 1)
        assert status["created"], "un ticket a été créé pour le finding"
        await handle.signal("stop", {})
        await handle.result()

    from choregos_api.db.models import Finding
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = await session.get(Finding, finding_id)
        assert row is not None and row.status == "created"
        assert row.created_work_item_id is not None
    created = setup.adapters.tracker.items
    assert any("[perf]" in item.title for item in created.values())
    assert setup.adapters.tracker.links, "le ticket créé est lié à son origine"


async def test_duplicate_finding_is_commented_not_recreated(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Un finding quasi identique ne crée pas un second ticket : il incrémente l'occurrence."""
    first = await _add_finding(setup, "Requête N+1 sur les lignes de commande", "src/orders/repository.py:88")
    second = await _add_finding(
        setup, "Requête N+1 sur les lignes de commande", "src/orders/repository.py:88"
    )
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "FindingsTriage",
            {"project_slug": setup.project_slug},
            id=f"findings-{setup.project_slug}",
            task_queue="test",
        )
        await handle.signal("finding", {"finding_id": first, "project_id": setup.project_id})
        await _wait(handle, lambda s: s["processed"] >= 1)
        await handle.signal("finding", {"finding_id": second, "project_id": setup.project_id})
        status = await _wait(handle, lambda s: s["processed"] >= 2)
        assert status["duplicates"] == 1, status
        assert len(status["created"]) == 1
        await handle.signal("stop", {})
        await handle.result()


async def test_provisioning_runs_steps_and_resumes(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Le provisioning exécute les étapes du template, et une reprise ne rejoue pas l'existant."""
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "ProjectProvisioning",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "org": "varga",
                "template_ref": "github-tekton-argo-k8s@1.0.0",
            },
            id=f"prov-{setup.project_slug}",
            task_queue="test",
        )
        result = await handle.result()

    assert result["status"] in {"succeeded", "failed"}
    assert result["completed"], "au moins une étape exécutée"

    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        steps = project.provision_state["steps"]
        assert all(step["status"] in {"succeeded", "failed", "skipped"} for step in steps)

    # Relance : les étapes déjà réussies sont sautées
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "ProjectProvisioning",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "template_ref": "github-tekton-argo-k8s@1.0.0",
                "resume": True,
            },
            id=f"prov-{setup.project_slug}-2",
            task_queue="test",
        )
        second = await handle.result()
    assert second["status"] == result["status"]


async def test_provisioning_dry_run_lists_steps(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "ProjectProvisioning",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "template_ref": "github-tekton-argo-k8s@1.0.0",
                "dry_run": True,
            },
            id=f"prov-dry-{setup.project_slug}",
            task_queue="test",
        )
        result = await handle.result()
    assert result["dry_run"] is True
    assert isinstance(result["steps"], list)


# ──────── a notifier failure never undoes a finding (#497) ────────

NO_SLACK = "[slack] ni `bot_token` ni `webhook_url` configurés"  # lang: fr-ok (the verbatim upstream error)


async def test_a_critical_finding_without_a_notifier_is_still_filed(setup: Fixture) -> None:
    """Raising from the notification rolled back the finding after its ticket was created."""
    from choregos_api.db.models import Finding
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import findings as findings_activities

    setup.adapters.notify.panne = NO_SLACK
    finding_id = await _add_finding(
        setup, "Secret in a log line", "src/auth/login.py:12 — token logged", severity="critical"
    )

    outcome = await findings_activities.triage_finding(
        {"project_slug": setup.project_slug, "finding_id": finding_id}
    )

    assert outcome["status"] == "created", outcome
    assert outcome["notified"] is False, "the silence must be said, not guessed"
    async with session_scope() as session:
        row = await session.get(Finding, finding_id)
        assert row is not None and row.status == "created"


async def test_the_false_positive_alert_without_a_notifier_still_answers(setup: Fixture) -> None:
    from choregos_api.db.models import Finding
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import findings as findings_activities

    for index in range(3):
        finding_id = await _add_finding(setup, f"Noise {index}", "nothing to act on")
        async with session_scope() as session:
            row = await session.get(Finding, finding_id)
            assert row is not None
            row.relevant = False
    setup.adapters.notify.panne = NO_SLACK

    outcome = await findings_activities.finding_quality_ratio({"project_slug": setup.project_slug})

    assert outcome["alert"] is True, outcome
    assert outcome["notified"] is False, outcome


# ──────── a memory failure never files a finding twice (S22-35b) ────────


async def test_a_memory_timeout_does_not_undo_a_filed_finding(setup: Fixture) -> None:
    """The memory's `ReadTimeout` rolled back the finding after its ticket was created, and the
    activity's retry filed the same ticket again (dev tenant, 2026-10-18)."""
    import httpx
    from choregos_api.db.models import Finding
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities import findings as findings_activities

    async def timeout(*args: Any, **kwargs: Any) -> None:
        raise httpx.ReadTimeout("memory did not answer")

    setup.adapters.memory.write_fact = timeout  # type: ignore[method-assign]
    tracker = setup.adapters.tracker

    def tickets() -> int:
        return sum(1 for name, _ in tracker.calls if name == "create_item")

    created_before = tickets()
    finding_id = await _add_finding(setup, "Typecheck target runs no checker", "Makefile:20 prints a message")
    payload = {"project_slug": setup.project_slug, "finding_id": finding_id}

    outcome = await findings_activities.triage_finding(payload)
    assert outcome["status"] == "created", outcome
    assert outcome["remembered"] is False, "the silence must be said, not guessed"
    async with session_scope() as session:
        row = await session.get(Finding, finding_id)
        assert row is not None and row.status == "created"

    # What Temporal does after a failed attempt: the same payload once more.
    again = await findings_activities.triage_finding(payload)
    assert again.get("reused") is True, again
    assert tickets() == created_before + 1, "one finding, one ticket"
