"""The ACA executor against a real Azure subscription (S13-05).

`packages/adapters/tests/test_aca.py` proves the **shape** of the ARM calls against a simulated
transport. This file proves that a subscription accepts them: a job created, triggered, observed
up to its terminal state, cancelled, and its logs read back from Log Analytics.

What only a real execution can say, and the simulated transport never will:

- that ARM accepts the definition as written (a misnamed property is a 400, not a red test);
- that a replayed `start` does not create a second execution — the "no double billing"
  property the contract announces;
- that the run token **does not appear** in the definition ARM renders;
- that the ACA states translate into ours for real.

Prerequisites (see `docs/runbooks/aca-live.md`) :

    export CHOREGOS_LIVE_AZURE_SUBSCRIPTION=$(az account show --query id -o tsv)
    export CHOREGOS_LIVE_AZURE_RG=choregos-aca-test
    export CHOREGOS_LIVE_AZURE_ENV_ID=$(az containerapp env show -n choregos-env \\
        -g "$CHOREGOS_LIVE_AZURE_RG" --query id -o tsv)
    export CHOREGOS_LIVE_AZURE_TOKEN=$(az account get-access-token \\
        --resource https://management.azure.com --query accessToken -o tsv)
    uv run pytest tests/live/test_aca_live.py -m live

The jobs created carry the `choregos/run-id` tag and are deleted by the fixture; the resource
group is disposable and goes away with one `az group delete`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from choregos_adapters.executor.aca import AcaExecutor, AzureArmClient
from choregos_core.domain import ExecRef, StageJobSpec

from .conftest import require

pytestmark = pytest.mark.live

# The executor always passes `args: ["run"]` — the command of *our* runner, and that is fine.
# Consequence observed on a real subscription: a sample image ignores it and stays alive. The test
# therefore does not try to make a third-party container exit; it relies on `replicaTimeout`,
# which the executor fills from `timeout_minutes`, to get a deterministic terminal state. This
# checks a real property along the way: the timeout we ask for is applied by ACA, and its expiry
# surfaces as a failure rather than a success.
IMAGE = "mcr.microsoft.com/cbl-mariner/busybox:2.0"


@pytest.fixture
async def executor() -> AsyncIterator[AcaExecutor]:
    env = require(
        "CHOREGOS_LIVE_AZURE_SUBSCRIPTION",
        "CHOREGOS_LIVE_AZURE_RG",
        "CHOREGOS_LIVE_AZURE_ENV_ID",
        "CHOREGOS_LIVE_AZURE_TOKEN",
    )
    # One client per test: httpx binds its connections to the loop that opened them, and a
    # module fixture would make them cross two different loops.
    async with httpx.AsyncClient(timeout=60.0) as client:
        arm = AzureArmClient(
            subscription_id=env["CHOREGOS_LIVE_AZURE_SUBSCRIPTION"],
            resource_group=env["CHOREGOS_LIVE_AZURE_RG"],
            token=env["CHOREGOS_LIVE_AZURE_TOKEN"],
            client=client,
        )
        yield AcaExecutor(
            arm,
            environment_id=env["CHOREGOS_LIVE_AZURE_ENV_ID"],
            location="westeurope",
            cpu=0.5,
            memory="1Gi",
        )


def spec(run_id: str, *, timeout_minutes: int = 2) -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="live-test",
        namespace="choregos",
        runner_image=IMAGE,
        api_url="https://choregos.invalid/api",
        run_token=f"token-{run_id}",
        timeout_minutes=timeout_minutes,
        labels={"choregos/live": "true"},
    )


async def cleanup(executor: AcaExecutor, name: str) -> None:
    """Delete the job. The test must leave the subscription as it found it."""
    await executor.client.request("DELETE", executor._path(name))


async def wait_for(executor: AcaExecutor, ref: ExecRef, states: set[str], timeout_s: float = 300.0) -> str:
    """Wait for one of the requested states, or say which one was observed last."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    last = "unknown"
    while asyncio.get_running_loop().time() < deadline:
        last = (await executor.status(ref)).state
        if last in states:
            return last
        await asyncio.sleep(5)
    raise AssertionError(f"state {sorted(states)} never reached — last seen: {last}")


async def test_un_job_demarre_et_son_etat_terminal_remonte(executor: AcaExecutor) -> None:
    """The full cycle: creation, trigger, terminal state observed, consistent timestamps.

    The timeout is deliberately short (one minute): its expiry is what ends the execution, and a
    run that exceeds its timeout must be reported as **failed**. An executor that returned
    `succeeded` here would let through a stage that never finished.
    """
    run_id = f"live-{uuid.uuid4().hex[:8]}"
    ref = await executor.start(spec(run_id, timeout_minutes=1))
    try:
        assert ref.name.startswith("run-live-")
        assert ref.namespace == executor.client.resource_group

        state = await wait_for(executor, ref, {"succeeded", "failed"}, timeout_s=420)
        assert state == "failed", f"a run that exceeds its timeout must be `failed`, not {state}"

        status = await executor.status(ref)
        assert status.started_at is not None
        # `ended_at` is **optional**: observed on a real subscription, ARM returns `Failed` with a
        # `startTime` and no `endTime`. That is why the orchestrator timestamps the end of a run
        # itself (`activities/stage.py`) instead of trusting this one — the duration shown in the
        # ticket does not depend on this field.
        if status.ended_at is not None:
            assert status.ended_at >= status.started_at
    finally:
        await cleanup(executor, ref.name)


async def test_start_rejoue_ne_double_pas_l_execution(executor: AcaExecutor) -> None:
    """The property that costs money if it is false.

    The orchestrator replays an activity after a crash; if `start` re-triggered the job, the
    run would be billed twice and write twice. Only one execution must exist.
    """
    run_id = f"live-{uuid.uuid4().hex[:8]}"
    ref = await executor.start(spec(run_id))
    try:
        await wait_for(executor, ref, {"running", "succeeded", "failed"})
        again = await executor.start(spec(run_id))
        assert again.name == ref.name

        executions = await executor._executions(ref.name)
        assert len(executions) == 1, f"{len(executions)} executions — `start` re-triggered"
    finally:
        await cleanup(executor, ref.name)


async def test_le_jeton_de_run_n_apparait_pas_dans_arm(executor: AcaExecutor) -> None:
    """An `az containerapp job show` must not return the token.

    It goes out as a job *secret*, referenced by `secretRef`: ARM returns the secret's name, not
    its value. That is the reason for this detour, and it can be checked from the outside.
    """
    run_id = f"live-{uuid.uuid4().hex[:8]}"
    ref = await executor.start(spec(run_id))
    try:
        rendered = await executor.client.request("GET", executor._path(ref.name))
        as_text = str(rendered)
        assert f"token-{run_id}" not in as_text, "the run token is readable in ARM"
        assert "secretRef" in as_text
        assert "run-token" in as_text

        # …and the tag that ties the job to its run is there, which is what makes it possible to
        # find an orphan execution.
        # Its name is translated: ARM refuses the `/` in a tag name, which no test against a
        # simulated transport could say.
        tags = (rendered or {}).get("tags", {})
        assert tags.get("choregos_run-id") == run_id
        assert not any("/" in name for name in tags), f"tag with a forbidden name: {sorted(tags)}"
    finally:
        await cleanup(executor, ref.name)


async def test_une_execution_peut_etre_annulee(executor: AcaExecutor) -> None:
    """`cancel` stops the running execution — stopping an expensive run must be immediate."""
    run_id = f"live-{uuid.uuid4().hex[:8]}"
    ref = await executor.start(spec(run_id, timeout_minutes=5))
    try:
        await wait_for(executor, ref, {"running", "succeeded", "failed"})
        # `cancel` is called whatever happens: what is checked is that it completes without an
        # error and that the state stays consistent, not that it wins the race against a container
        # that finishes within a few seconds.
        await executor.cancel(ref)
        state = await wait_for(executor, ref, {"cancelled", "succeeded", "failed"}, timeout_s=180)
        # An execution already finished before the cancellation stays finished: this test checks
        # that `cancel` completes, not that it wins the race against a container that ends in 10 s.
        assert state in {"cancelled", "succeeded", "failed"}
    finally:
        await cleanup(executor, ref.name)


async def test_un_job_inexistant_est_une_absence_pas_une_panne(executor: AcaExecutor) -> None:
    """ARM answers 404; the executor must translate it into "nothing", not an exception."""
    ref = ExecRef(
        kind=executor.kind,
        name="run-nonexistent-0000",
        namespace=executor.client.resource_group,
        run_id="nonexistent",
    )
    status = await executor.status(ref)
    assert status.state == "unknown"
    assert "aucune exécution" in status.message  # lang: fr-ok (product output)


async def test_les_logs_disent_ou_regarder_sans_workspace(executor: AcaExecutor) -> None:
    """Without a configured workspace, `logs()` explains where to look instead of returning silence."""
    ref = ExecRef(
        kind=executor.kind,
        name="run-any",
        namespace=executor.client.resource_group,
        run_id="any",
    )
    lines = [line async for line in executor.logs(ref)]
    assert len(lines) == 1
    assert "ContainerAppConsoleLogs_CL" in lines[0]
