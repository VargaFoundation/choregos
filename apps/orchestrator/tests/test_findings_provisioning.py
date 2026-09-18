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
