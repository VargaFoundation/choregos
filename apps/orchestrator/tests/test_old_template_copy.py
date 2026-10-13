"""A project that stored a copy of an old core template still completes its refine step (S31-02).

Every declared output is now required. The old `default-simple` declared `spec` and
`allowed_paths`, which nothing produces: a project whose modified copy still declares them (the
data migration only upgrades untouched copies) must not start failing at refine.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture
from .test_interpreter import _wait_state, start

pytestmark = pytest.mark.asyncio


async def _store_old_copy(setup: Fixture) -> None:
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope
    from choregos_core.dsl.legacy import legacy_declarations, old_template
    from sqlalchemy import select

    document = old_template("default-simple")
    # Modified by a person: the migration leaves it alone.
    document["transitions"][0]["timeout_hours"] = 12
    assert "t-refine: spec" in legacy_declarations(document)
    async with session_scope() as session:
        row = (
            await session.execute(
                select(WorkflowDef).where(WorkflowDef.project_id == setup.project_id, WorkflowDef.is_active)
            )
        ).scalar_one()
        row.json_doc = document


async def test_an_old_modified_copy_completes_refine_on_the_fake_executor(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _store_old_copy(setup)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        # Nothing scripted: the fake executor's default result, as on a demo deployment.
        await _wait_state(handle, "awaiting_spec_approval")
        status = await handle.query("status")
        assert status["attempts"].get("t-refine", 0) <= 1, status
        await handle.signal("control", {"action": "stop"})
        await handle.result()
    started = setup.adapters.executor.started[0].stage_input
    assert started is not None and "spec" in started.transition.outputs, "the old copy was the one played"
