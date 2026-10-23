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


async def test_no_setting_means_enabled_for_claude_code_only(setup: Fixture) -> None:
    """A fresh install has no `agent_backends` row: only `claude-code` is on, as the console says."""
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.stage import _require_backend_enabled

    async with session_scope() as session:
        await _require_backend_enabled(session, None, "claude-code")
        with pytest.raises(ApplicationError) as refused:
            await _require_backend_enabled(session, None, "gemini-cli")
    assert refused.value.type == "BackendDisabled"
    assert "gemini-cli" in str(refused.value)


async def test_the_listing_and_the_orchestrator_agree_on_the_same_rows(setup: Fixture) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.routers.admin import list_backends
    from choregos_orchestrator.activities.stage import _require_backend_enabled

    await _switch("claude-code", enabled=False)
    await _switch("gemini-cli", enabled=True)  # opencode, codex...: no row
    async with session_scope() as session:
        listed = {b.name: b.enabled for b in await list_backends(session, None)}  # type: ignore[arg-type]
        for name, enabled in listed.items():
            try:
                await _require_backend_enabled(session, None, name)
                decided = True
            except ApplicationError:
                decided = False
            assert decided == enabled, name
    assert listed["claude-code"] is False and listed["gemini-cli"] is True and listed["opencode"] is False


async def test_the_reviewer_swap_skips_a_disabled_backend(setup: Fixture) -> None:
    from choregos_api.db.session import session_scope
    from choregos_core import ModelResolver
    from choregos_orchestrator.activities import stage as st
    from choregos_orchestrator.activities.base import project_bundle

    await _switch("gemini-cli", enabled=False)
    await _switch("opencode", enabled=True)
    async with session_scope() as session:
        bundle = await project_bundle(session, setup.project_id)
        item = type("I", (), {"id": setup.work_item_id, "size": "m", "tracker_key": "K-1"})()
        from choregos_api.db.models import Run

        session.add(
            Run(
                id="run-impl",
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                status="succeeded",
                backend="claude-code",
            )
        )
        await session.flush()
        bundle.config.agent.allowed_backends = ["claude-code", "gemini-cli", "opencode"]
        resolver = ModelResolver(gateway_url="", gateway_models=[], validated_backends={})
        plan = st.StagePlan(**(_plan(setup) | {"role": "review"}))
        chosen = await st._reviewer_backend(session, bundle, item, "claude-code", resolver, plan)
    assert chosen != "gemini-cli"


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
