"""The platform's agent-backend switch is applied at stage start (S42-07c).

S42-07 stored `PUT /platform/backends` and showed it; nothing read it, so a backend the
administrator disabled still ran. `prepare_stage` now refuses the stage, before any budget, key or
pod, with a typed `BackendDisabled` error the interpreter escalates like any configuration refusal.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture, refine_result
from .test_budget_reservation import _plan
from .test_interpreter import _runs, _wait_state, _wait_until, scripted, start
from .test_reprise_apres_escalade import _demandes

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


async def _switch(name: str, *, enabled: bool, reason: str | None = None) -> None:
    """What `PUT /platform/backends` stores."""
    from choregos_api.db.models import BackendRegistryRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(select(BackendRegistryRow).where(BackendRegistryRow.name == name))
        ).scalar_one_or_none()
        if row is None:
            row = BackendRegistryRow(name=name, capabilities=[])
            session.add(row)
        row.enabled = enabled
        row.disabled_reason = reason


async def _planned_backend(setup: Fixture) -> str:
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import project_bundle

    async with session_scope() as session:
        return str((await project_bundle(session, setup.project_id)).config.agent.default_backend)


async def test_a_disabled_backend_refuses_the_stage_and_starts_nothing(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    backend = await _planned_backend(setup)
    await _switch(backend, enabled=False, reason="under security review")
    with pytest.raises(ApplicationError) as refused:
        await prepare_stage(_plan(setup))
    assert refused.value.type == "BackendDisabled"
    assert refused.value.non_retryable
    assert backend in str(refused.value)
    assert "disabled by the platform administrator" in str(refused.value)
    assert "under security review" in str(refused.value)
    assert await _runs(setup) == 0
    assert setup.adapters.executor.started == [], "no runner pod"


async def test_an_enabled_backend_runs(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _switch(await _planned_backend(setup), enabled=True)
    assert "refused" not in await prepare_stage(_plan(setup))


async def test_no_setting_means_enabled(setup: Fixture) -> None:
    """A fresh install has no `agent_backends` row: nothing was switched off."""
    from choregos_orchestrator.activities.stage import prepare_stage

    assert "refused" not in await prepare_stage(_plan(setup))


async def test_another_backend_disabled_does_not_matter(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    backend = await _planned_backend(setup)
    await _switch("opencode" if backend != "opencode" else "gemini-cli", enabled=False)
    assert "refused" not in await prepare_stage(_plan(setup))


async def test_a_disabled_backend_escalates_then_runs_once_enabled(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    backend = await _planned_backend(setup)
    await _switch(backend, enabled=False)
    scripted(setup.adapters, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "the work item waits for a person", lambda s: s["pending_request"])
        assert await _runs(setup) == 0
        assert setup.adapters.executor.started == []
        asked = [d for d in await _demandes(setup) if (d.payload or {}).get("reason") == "configuration"]
        assert asked and "disabled by the platform administrator" in asked[0].payload["question"], asked

        await _switch(backend, enabled=True)  # the administrator turns it back on
        await handle.signal("control", {"action": "rerun_stage", "by": "operator"})
        await _wait_state(handle, "awaiting_spec_approval")
        assert await _runs(setup) == 1
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-backend-disabled-S42-07c.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
