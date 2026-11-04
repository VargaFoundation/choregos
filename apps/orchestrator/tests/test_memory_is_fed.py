"""The memory is fed (S51-02).

`MemoryIngestion` was registered and never started, so no project's memory was ever ingested, and
`write_run_lesson` was never called, so no failed run left a lesson. Now: the worker starts one loop
per active project whose policy keeps memory, an activation starts the project's, and a failed run's
outcome writes its lesson once (`run:<id>`, the id the `run_lessons` source uses).
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_orchestrator.train_client import clear_fake_signals, fake_starts

from .conftest import Fixture


async def _disable_memory(setup: Fixture) -> None:
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(
                    PolicyDef.project_id == setup.project_id, PolicyDef.is_active.is_(True)
                )
            )
        ).scalar_one()
        row.json_doc = {
            **row.json_doc,
            "memory": {**dict(row.json_doc.get("memory") or {}), "enabled": False},
        }


async def _set_project(setup: Fixture, **fields: Any) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        for name, value in fields.items():
            setattr(project, name, value)


async def test_the_memory_loop_of_a_project_starts_once(setup: Fixture) -> None:
    from choregos_orchestrator.train_client import start_memory_loop

    clear_fake_signals()
    assert await start_memory_loop("billing-api") is True
    assert await start_memory_loop("billing-api") is False, "one loop per project"
    assert fake_starts() == {"mem-billing-api": {"project_slug": "billing-api"}}


async def test_the_worker_starts_a_loop_per_active_project_that_keeps_memory(
    setup: Fixture, monkeypatch: Any
) -> None:
    from choregos_orchestrator import worker

    asked: list[tuple[str, str, bool]] = []

    async def capture(
        name: str, workflow_id: str, payload: dict[str, Any], *, relancer_si_echoue: bool = False
    ) -> bool:
        asked.append((name, workflow_id, relancer_si_echoue))
        return True

    monkeypatch.setattr("choregos_orchestrator.train_client.start_workflow_once", capture)
    assert await worker.start_memory_loops() == ["billing-api"]
    assert asked == [("MemoryIngestion", "mem-billing-api", True)], "a loop that died in failure restarts"

    asked.clear()
    await _disable_memory(setup)
    assert await worker.start_memory_loops() == []
    assert asked == [], "a policy without memory gets no loop"


@pytest.mark.parametrize("memory", [True, False])
async def test_a_project_activated_by_its_provisioning_gets_its_memory_loop(
    setup: Fixture, memory: bool
) -> None:
    from choregos_orchestrator.activities.provisioning import finish_provisioning

    clear_fake_signals()
    if not memory:
        await _disable_memory(setup)
    step = {"name": "github.ensure_labels", "status": "succeeded"}
    await _set_project(setup, status="provisioning", provision_state={"status": "running", "steps": [step]})

    await finish_provisioning({"project_id": setup.project_id})

    assert ("mem-billing-api" in fake_starts()) is memory


async def _failed_run(setup: Fixture, run_id: str = "run-failed") -> str:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(
            Run(
                id=run_id,
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                attempt=2,
                status="running",
            )
        )
    return run_id


async def _record_failure(run_id: str) -> dict[str, Any]:
    from choregos_contracts import StageResult
    from choregos_orchestrator.activities.outcome import record_run_outcome

    result = StageResult.failure("the tests did not pass", "tests")
    return await record_run_outcome(
        {"run_id": run_id, "result": result.model_dump(mode="json", by_alias=True)}
    )


async def test_a_failed_run_leaves_its_lesson_once(setup: Fixture) -> None:
    from choregos_orchestrator.activities.memory import ingest_sources

    run_id = await _failed_run(setup)
    assert (await _record_failure(run_id))["status"] == "failed"
    facts = setup.adapters.memory.facts["billing-api"]
    assert [(m.subject, m.content) for m in facts] == [
        ("run_lesson:billing-api:implement", "The implement step failed (tests) — attempt 2.")
    ]
    assert facts[0].provenance["external_id"] == f"run:{run_id}"

    await _record_failure(run_id)  # a replayed activity
    await ingest_sources({"project_slug": "billing-api", "sources": ["run_lessons"]})  # the loop's source
    assert len(setup.adapters.memory.facts["billing-api"]) == 1, "the same lesson, kept once"


async def test_a_failed_run_leaves_no_lesson_under_a_policy_without_memory(setup: Fixture) -> None:
    await _disable_memory(setup)
    run_id = await _failed_run(setup)
    assert (await _record_failure(run_id))["status"] == "failed"
    assert not setup.adapters.memory.facts.get("billing-api")


async def test_a_lesson_that_cannot_be_written_does_not_fail_the_outcome(setup: Fixture) -> None:
    async def down(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("memory down")

    setup.adapters.memory.ingest_events = down
    run_id = await _failed_run(setup)
    assert (await _record_failure(run_id))["recorded"] is True, "memory never blocks a stage (ADR 0007)"


async def test_an_alert_is_not_kept_under_a_policy_without_memory(setup: Fixture) -> None:
    from choregos_orchestrator.activities.memory import ingest_alert

    await _disable_memory(setup)
    event = {"delivery_id": "f-1", "payload": {"labels": {"alertname": "ErrorBudget"}}}
    assert (await ingest_alert({"project_slug": "billing-api", "event": event}))["ingested"] is False
    assert not setup.adapters.memory.facts.get("billing-api")
