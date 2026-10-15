"""A queued run whose interpreter dies leaves neither its Job nor its Secret behind (#501).

A run that waits for a slot is a suspended Job (`k8s_job`): it never finishes, so its
`ttlSecondsAfterFinished` never starts, and Kubernetes stops `activeDeadlineSeconds` while a Job
is suspended. Its Secret (run token, gateway key) is owned by the Job (#499), so it stayed as long
as the Job did: forever, once nobody polled the run. A dying interpreter
(`record_workflow_failure`) now deletes the Job of a run still queued, and the Secret goes with it.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.executor.k8s_job import KubernetesJobExecutor
from choregos_adapters.fakes.kubernetes import FakeKubernetesApi

from .conftest import Fixture
from .test_gateway_key_through_the_secret import _prepare_and_start, _run

pytestmark = pytest.mark.asyncio


async def _die(setup: Fixture) -> None:
    from choregos_orchestrator.activities.interpretation import record_workflow_failure

    await record_workflow_failure(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "message": "await_run: schedule-to-close timeout",
            "activity": "await_run",
        }
    )


def _cluster(setup: Fixture) -> FakeKubernetesApi:
    api = FakeKubernetesApi()
    setup.adapters.executor = KubernetesJobExecutor(client=api, max_active=1)  # type: ignore[arg-type]
    return api


async def test_a_dying_interpreter_removes_the_job_and_secret_of_its_queued_run(setup: Fixture) -> None:
    api = _cluster(setup)
    running = await _prepare_and_start(setup, attempt=1)
    queued = await _prepare_and_start(setup, attempt=2)
    assert (await _run(queued)).status == "queued"
    assert api.jobs[f"run-{queued}"]["spec"]["suspend"] is True
    assert f"run-{queued}" in api.secrets

    await _die(setup)

    assert f"run-{queued}" not in api.jobs, "a suspended Job never ends on its own"
    assert f"run-{queued}" not in api.secrets, "the token and gateway key outlived the run"
    assert (await _run(queued)).status == "failed"
    # A running run is left to its own deadline: its Job ends, and its TTL takes its Secret.
    assert f"run-{running}" in api.jobs and f"run-{running}" in api.secrets
    assert (await _run(running)).status == "running"


async def test_a_queued_run_abandoned_twice_is_not_an_error(setup: Fixture) -> None:
    """`record_workflow_failure` is retried (ADR 0008): its Job is already gone the second time."""
    api = _cluster(setup)
    await _prepare_and_start(setup, attempt=1)
    queued = await _prepare_and_start(setup, attempt=2)
    await _die(setup)
    await _die(setup)
    assert f"run-{queued}" not in api.jobs and f"run-{queued}" not in api.secrets


async def test_a_queued_run_is_removed_before_its_key_is_revoked(setup: Fixture, monkeypatch: Any) -> None:
    """As in `cancel_run`: the executor is told to stop first, then the key is revoked."""
    from choregos_orchestrator.activities import interpretation

    order: list[str] = []
    api = _cluster(setup)
    await _prepare_and_start(setup, attempt=1)
    queued = await _prepare_and_start(setup, attempt=2)
    real_close = interpretation.close_run_spend

    async def close(project_id: str, run_id: str) -> None:
        order.append(f"close:{run_id}:{'job' if f'run-{run_id}' in api.jobs else 'gone'}")
        await real_close(project_id, run_id)

    monkeypatch.setattr(interpretation, "close_run_spend", close)
    await _die(setup)
    assert f"close:{queued}:gone" in order
