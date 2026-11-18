"""The memory loop carries the `run_lessons` cursor from one pass to the next (and across a continue-as-new).

Without it every pass (15 minutes) re-sent every failed run ever: one more version, one more embedding
call, one more pending proposal each time (prod, 2026-11-18).
"""

from __future__ import annotations

import contextlib
from typing import Any

from temporalio import activity

PAYLOAD = {"project_slug": "replay", "sources": ["run_lessons"], "max_cycles": 2}


async def test_the_second_pass_asks_only_for_what_came_after_the_first() -> None:
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import WorkflowContinuedAsNewError
    from temporalio.converter import default
    from temporalio.testing import WorkflowEnvironment
    from tranibor_orchestrator.testing import workers_by_queue
    from tranibor_orchestrator.workflows.memory import MemoryIngestion

    carried: dict[str, Any] = {}
    asked: list[dict[str, Any]] = []

    @activity.defn(name="ingest_sources")
    async def ingest_sources(payload: dict[str, Any]) -> dict[str, Any]:
        asked.append(payload)
        return {"facts": 1, "run_lessons_watermark": f"2026-11-18T10:0{len(asked)}:00+00:00"}

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_by_queue(
            environment.client, activities=[ingest_sources], workflows=[MemoryIngestion]
        ):
            handle = await environment.client.start_workflow(
                "MemoryIngestion", PAYLOAD, id="mem-replay-cursor", task_queue="test"
            )
            with contextlib.suppress(WorkflowContinuedAsNewError):
                await handle.result(follow_runs=False)
            first = environment.client.get_workflow_handle(
                "mem-replay-cursor", run_id=handle.first_execution_run_id
            )
            history = await first.fetch_history()
            for event in history.events:
                if event.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW:
                    attributes = event.workflow_execution_continued_as_new_event_attributes
                    carried = default().payload_converter.from_payloads(attributes.input.payloads)[0]
            await environment.client.get_workflow_handle("mem-replay-cursor").signal("stop", {})
    finally:
        await environment.shutdown()
    assert "run_lessons_since" not in asked[0], "the first pass reads everything"
    assert asked[1]["run_lessons_since"] == "2026-11-18T10:01:00+00:00"
    assert carried["run_lessons_since"] == "2026-11-18T10:02:00+00:00", "the cursor survives continue-as-new"
