"""S62-19: provisioning a project AGAIN, on the same workflow id, after a run that succeeded.

`start_provisioning` (S62-15) starts a new run of `prov-<slug>` after a closed one, so a second
provisioning re-runs the template's steps. ADR 0008 says every step is replayable; this proves it on
the real template (`github-tekton-argo-k8s`, thirteen steps) and the platform's fakes: the second run
succeeds, the project ends `active`, and nothing is created twice.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

TEMPLATE = "github-tekton-argo-k8s@1.0.0"


async def _provision(setup: Fixture, temporal_env: Any, worker_factory: Any, **extra: Any) -> dict[str, Any]:
    """One run of `prov-<slug>`, started as the API starts it (`ALLOW_DUPLICATE`)."""
    from temporalio.common import WorkflowIDReusePolicy

    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "ProjectProvisioning",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "org": "northwind",
                "template_ref": TEMPLATE,
                **extra,
            },
            id=f"prov-{setup.project_slug}",
            task_queue="test",
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
        result: dict[str, Any] = await handle.result()
        return result


def _effects(setup: Fixture) -> dict[str, Any]:
    """What the steps left on the fakes: the things a double effect would multiply."""
    adapters = setup.adapters
    subject = f"convention:{setup.project_slug}:bootstrap"
    return {
        "board_fields": list(adapters.tracker.board_fields),
        "gateway_teams": sorted(adapters.gateway.teams),
        "bootstrap_facts": [
            m
            for m in adapters.memory.facts.get(setup.project_slug, [])
            if m.subject == subject and m.status == "active"
        ],
        "initial_import": list(adapters.memory.ingested),
        "notifications": len(adapters.notify.sent),
    }


async def _state(setup: Fixture) -> tuple[str, dict[str, Any]]:
    from tranibor_api.db.models import Project
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        return project.status, dict(project.provision_state)


async def test_a_second_provisioning_resumes_and_replays_nothing(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """What `POST /provision` does by default (`resume: true`): the steps that succeeded are skipped."""
    first = await _provision(setup, temporal_env, worker_factory)
    assert first["status"] == "succeeded" and not first["failed"]
    assert len(first["completed"]) >= 2, "the template has several steps"
    before = _effects(setup)
    calls = len(setup.adapters.tracker.calls)

    second = await _provision(setup, temporal_env, worker_factory)

    assert second["status"] == "succeeded" and not second["failed"]
    assert _effects(setup) == before
    assert len(setup.adapters.tracker.calls) == calls, "no step reached the tracker again"
    status, state = await _state(setup)
    assert status == "active" and state["status"] == "succeeded"


async def test_a_second_provisioning_that_replays_every_step_has_no_double_effect(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`resume: false`: every step runs again, on the same workflow id, and nothing is doubled."""
    first = await _provision(setup, temporal_env, worker_factory)
    assert first["status"] == "succeeded"
    before = _effects(setup)
    calls = len(setup.adapters.tracker.calls)
    assert before["board_fields"] and before["gateway_teams"] and before["bootstrap_facts"]
    assert before["initial_import"], "the steps left something to double"

    second = await _provision(setup, temporal_env, worker_factory, resume=False)

    assert second["status"] == "succeeded" and not second["failed"]
    assert second["completed"] == first["completed"], "every step ran again, none was skipped"
    assert len(setup.adapters.tracker.calls) > calls, "the board step really reached the tracker again"
    after = _effects(setup)
    for kept in ("board_fields", "gateway_teams", "initial_import"):
        assert after[kept] == before[kept], f"{kept} doubled"
    assert len(after["bootstrap_facts"]) == 1, "one active bootstrap fact: the new one supersedes the old"
    # `notify.test_message` exists to prove the notifier works: it sends each time, by design.
    assert after["notifications"] == before["notifications"] + 1

    status, state = await _state(setup)
    assert status == "active" and state["status"] == "succeeded"
    names = [step["name"] for step in state["steps"]]
    assert len(names) == len(set(names)), "one state entry per step, not one per run"
    assert all(step["status"] == "succeeded" for step in state["steps"])
