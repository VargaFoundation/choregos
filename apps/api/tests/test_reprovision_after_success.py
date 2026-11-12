"""Provisioning a project again after a run that ended starts a new run (production, 2026-11-12).

`prov-<slug>` is one Temporal id per project. Under the policy every other start uses (a new run only
after a FAILED one), a second "provision" on a project whose first run had succeeded was refused by
Temporal; `_start` reads that refusal as "already started", the request still wrote `provisioning` /
`running`, and nothing ever finished the project: `a2a-smoke` on the prod tenant stayed `provisioning`
after S62-13's fix was deployed, because its first run had succeeded. `FakeTemporal` never refuses, so
only a real server shows it. Uses a local Temporal dev server (`WorkflowEnvironment.start_local`), or
the one `CHOREGOS_TEST_TEMPORAL_ADDRESS` names.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from temporalio import workflow


@workflow.defn(name="ProjectProvisioning")
class _Provisioning:
    """Stands for the orchestrator's: ends at once when told to, waits for a signal otherwise."""

    def __init__(self) -> None:
        self.release = False

    @workflow.signal
    def finish(self) -> None:
        self.release = True

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> str:
        if payload.get("hold"):
            await workflow.wait_condition(lambda: self.release)
        return workflow.info().run_id


@pytest.fixture
async def env() -> AsyncIterator[Any]:
    from temporalio.client import Client
    from temporalio.testing import WorkflowEnvironment

    address = os.environ.get("CHOREGOS_TEST_TEMPORAL_ADDRESS")
    if address:
        namespace = os.environ.get("CHOREGOS_TEST_TEMPORAL_NAMESPACE", "default")
        yield WorkflowEnvironment.from_client(await Client.connect(address, namespace=namespace))
        return
    environment = await WorkflowEnvironment.start_local()
    try:
        yield environment
    finally:
        await environment.shutdown()


def _worker(env: Any, queue: str) -> Any:
    """Unsandboxed: the sandbox re-imports the workflow's module, which a test module is not."""
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    return Worker(
        env.client, task_queue=queue, workflows=[_Provisioning], workflow_runner=UnsandboxedWorkflowRunner()
    )


def _temporal(env: Any, queue: str) -> Any:
    from choregos_api.temporal import RealTemporal

    temporal = RealTemporal("unused:7233", env.client.namespace, queue)

    async def connected() -> Any:
        return env.client

    temporal.client = connected  # type: ignore[method-assign]
    return temporal


async def test_a_second_provisioning_after_a_succeeded_one_runs_again(env: Any) -> None:
    queue = f"reprovision-{uuid.uuid4().hex[:8]}"
    workflow_id = f"prov-smoke-{uuid.uuid4().hex[:8]}"
    temporal = _temporal(env, queue)
    async with _worker(env, queue):
        await temporal.start_provisioning(workflow_id, {})
        first = await env.client.get_workflow_handle(workflow_id).result()

        await temporal.start_provisioning(workflow_id, {})
        second = await env.client.get_workflow_handle(workflow_id).result()

    assert first != second, "the second provisioning never ran: the project stays `provisioning`"


async def test_a_provisioning_still_running_is_joined_not_restarted(env: Any) -> None:
    queue = f"reprovision-{uuid.uuid4().hex[:8]}"
    workflow_id = f"prov-smoke-{uuid.uuid4().hex[:8]}"
    temporal = _temporal(env, queue)
    async with _worker(env, queue):
        assert await temporal.start_provisioning(workflow_id, {"hold": True}) == workflow_id
        handle = env.client.get_workflow_handle(workflow_id)
        running = (await handle.describe()).run_id

        assert await temporal.start_provisioning(workflow_id, {"hold": True}) == workflow_id
        assert (await handle.describe()).run_id == running, "the run in progress was replaced"

        await handle.signal(_Provisioning.finish)
        assert await asyncio.wait_for(handle.result(), timedelta(seconds=30).total_seconds()) == running
