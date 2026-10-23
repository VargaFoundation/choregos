"""The relay of approved actions outside its fakes (ADR 0085, S40-19b, S40-19c).

`test_action_outbox.py` proves what one relay pass does against `FakeTemporal`. Here: the pass runs
because the API's lifespan starts it, and it holds against a real Temporal server — refused while
the server is unreachable, one run of `action-<id>` however many passes come. With the
orchestrator's workers on the queue (S40-19c), the relayed workflow runs the action's effect once,
and two replicas that both read the action as `approved` still run it once. In CI's second pass
(`CHOREGOS_TEST_SUITE_SUR_POSTGRES=1`) the same tests run on a migrated PostgreSQL under RLS.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

EFFECTS = [{"effect": "test.noop", "with": {}}]
TASK_QUEUE = "choregos-relay-test"


@pytest.fixture(autouse=True)
def noop_effect() -> Iterator[list[str]]:
    """The calls of the action's one effect: what the world outside saw, once or more."""
    from choregos_api import effets

    calls: list[str] = []

    async def noop(ctx: Any, _params: dict[str, Any]) -> dict[str, Any]:
        calls.append(str(ctx.action.id))
        return {}

    effets.declarer_un_effet("test.noop", noop)
    yield calls
    effets.reinitialiser()


@pytest.fixture
async def temporal_server() -> AsyncIterator[Any]:
    """A real Temporal dev server (the `temporal` CLI's, downloaded by the SDK on first use).

    `CHOREGOS_TEST_TEMPORAL_ADDRESS` (with `CHOREGOS_TEST_TEMPORAL_NAMESPACE`) points the tests at a
    server already running instead — the dev cluster's, through a port-forward, in a namespace of
    their own so that the platform's workers never see the test's task queues."""
    from temporalio.client import Client
    from temporalio.testing import WorkflowEnvironment

    address = os.environ.get("CHOREGOS_TEST_TEMPORAL_ADDRESS")
    if address:
        namespace = os.environ.get("CHOREGOS_TEST_TEMPORAL_NAMESPACE", "default")
        yield WorkflowEnvironment.from_client(await Client.connect(address, namespace=namespace))
        return
    env = await WorkflowEnvironment.start_local()
    try:
        yield env
    finally:
        await env.shutdown()


async def _stranded(client: AsyncClient, app: Any, pid: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Two actions proposed by a developer; the first approved while Temporal refused its start and
    left `approved` long enough for the relay, the second still pending."""
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import get_temporal
    from choregos_core import utcnow

    member = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"}
    )
    assert member.status_code in {200, 201}, member.text
    dev = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    ids: list[str] = []
    try:
        await login(dev, "dev@varga.dev")
        for n in range(2):
            body = {"kind": "test.noop", "title": f"Nothing {n}", "params": {}, "effects": EFFECTS}
            proposed = await dev.post(f"/api/v1/projects/{pid}/actions", json=body)
            assert proposed.status_code == 201, proposed.text
            ids.append(str(proposed.json()["id"]))
    finally:
        await dev.aclose()

    async def temporal_down(_workflow_id: str, _payload: dict[str, Any]) -> str:
        raise OSError("temporal: connection refused")

    with monkeypatch.context() as patch:
        patch.setattr(get_temporal(), "start_action", temporal_down)
        decided = await client.post(
            f"/api/v1/projects/{pid}/actions/{ids[0]}/decision", json={"decision": "approve"}
        )
    assert decided.status_code == 200 and decided.json()["status"] == "approved", decided.text
    async with session_scope(orgs="*") as session:
        row = await session.get(Action, ids[0])
        assert row is not None
        row.updated_at = utcnow() - timedelta(minutes=5)
    return ids


async def test_the_lifespan_runs_the_relay(
    client: AsyncClient, project: dict[str, Any], app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    stranded, pending = await _stranded(client, app, project["id"], monkeypatch)
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    assert fake.started == {}
    async with app.router.lifespan_context(app):
        for _ in range(100):
            if fake.started:
                break
            await asyncio.sleep(0.05)
    assert fake.started == {f"action-{stranded}": {"action_id": stranded}}, (
        "the API started without its relay: an approval whose start was lost stays approved for ever"
    )
    assert f"action-{pending}" not in fake.started


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.mark.slow
async def test_the_relay_starts_one_run_on_a_real_temporal(
    client: AsyncClient,
    project: dict[str, Any],
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
    temporal_server: Any,
) -> None:
    from choregos_api.services.action_relay import start_approved_actions
    from choregos_api.temporal import RealTemporal, set_temporal
    from temporalio.service import RPCError, RPCStatusCode

    stranded, pending = await _stranded(client, app, project["id"], monkeypatch)
    namespace = temporal_server.client.namespace

    set_temporal(RealTemporal(f"127.0.0.1:{_closed_port()}", namespace, TASK_QUEUE))
    assert await start_approved_actions(grace=timedelta(0)) == [], "nothing listens: nothing started"

    address = temporal_server.client.service_client.config.target_host
    set_temporal(RealTemporal(address, namespace, TASK_QUEUE))
    assert await start_approved_actions(grace=timedelta(0)) == [stranded], "the next pass relays it"
    handle = temporal_server.client.get_workflow_handle(f"action-{stranded}")
    first = await handle.describe()
    assert first.workflow_type == "ActionWorkflow"
    # Every replica relays: a second pass finds the workflow running and counts it as started.
    assert await start_approved_actions(grace=timedelta(0)) == [stranded]
    assert (await handle.describe()).run_id == first.run_id, "one run of action-<id>, however many passes"
    with pytest.raises(RPCError) as missing:
        await temporal_server.client.get_workflow_handle(f"action-{pending}").describe()
    assert missing.value.status == RPCStatusCode.NOT_FOUND, "a pending action is never started"


@pytest.fixture
def workers(temporal_server: Any) -> Any:
    """The orchestrator's workers on the test queue, one per queue as the chart deploys them."""
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES

    return workers_repartis(
        temporal_server.client,
        activites=[*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES],
        workflows=ALL_WORKFLOWS,
        file_du_workflow=TASK_QUEUE,
    )


async def _state(action_id: str) -> tuple[str, list[tuple[str, int]]]:
    from choregos_api.db.models import Action, ActionEffect
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        action = await session.get(Action, action_id)
        assert action is not None
        effects = (
            await session.execute(select(ActionEffect).where(ActionEffect.action_id == action_id))
        ).scalars()
        return action.status, [(e.status, e.attempts) for e in effects]


@pytest.mark.slow
async def test_a_worker_runs_the_relayed_action_once(
    client: AsyncClient,
    project: dict[str, Any],
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
    temporal_server: Any,
    workers: Any,
    noop_effect: list[str],
) -> None:
    from choregos_api.services.action_relay import start_approved_actions
    from choregos_api.temporal import RealTemporal, set_temporal

    stranded, pending = await _stranded(client, app, project["id"], monkeypatch)
    address = temporal_server.client.service_client.config.target_host
    set_temporal(RealTemporal(address, temporal_server.client.namespace, TASK_QUEUE))
    async with workers:
        assert await start_approved_actions(grace=timedelta(0)) == [stranded]
        handle = temporal_server.client.get_workflow_handle(f"action-{stranded}")
        assert await asyncio.wait_for(handle.result(), timeout=60) == {"status": "succeeded", "effects": [0]}
        # The next pass finds it `succeeded`, not `approved`: nothing to relay any more.
        assert await start_approved_actions(grace=timedelta(0)) == []
    assert noop_effect == [stranded], "the relayed approval ran its effect, once; the pending one never"
    assert await _state(stranded) == ("succeeded", [("done", 1)])
    assert (await _state(pending))[0] == "pending_approval"


@pytest.mark.slow
async def test_two_replicas_relaying_one_action_run_it_once(
    client: AsyncClient,
    project: dict[str, Any],
    app: Any,
    monkeypatch: pytest.MonkeyPatch,
    temporal_server: Any,
    workers: Any,
    noop_effect: list[str],
) -> None:
    """Two API replicas, each with its own Temporal client, read the same `approved` row before either
    starts it; one starts the workflow while the other is held, then — the run already finished —
    the other starts `action-<id>` too. The action runs once: a finished `action-<id>` is not
    started again (`ALLOW_DUPLICATE_FAILED_ONLY`), and a replica's late start counts as a start."""
    import contextvars

    from choregos_api import temporal as gateway
    from choregos_api.services.action_relay import start_approved_actions
    from choregos_api.temporal import RealTemporal

    stranded, _pending = await _stranded(client, app, project["id"], monkeypatch)
    address = temporal_server.client.service_client.config.target_host
    namespace = temporal_server.client.namespace
    replicas = [RealTemporal(address, namespace, TASK_QUEUE) for _ in range(2)]
    replica: contextvars.ContextVar[RealTemporal] = contextvars.ContextVar("replica")
    monkeypatch.setattr(gateway, "get_temporal", replica.get)

    both_read = asyncio.Barrier(2)
    first_done = asyncio.Event()
    late_start = replicas[1].start_action

    async def held(workflow_id: str, payload: dict[str, Any]) -> str:
        await both_read.wait()
        await first_done.wait()
        return await late_start(workflow_id, payload)

    early_start = replicas[0].start_action

    async def first(workflow_id: str, payload: dict[str, Any]) -> str:
        await both_read.wait()
        return await early_start(workflow_id, payload)

    monkeypatch.setattr(replicas[0], "start_action", first)
    monkeypatch.setattr(replicas[1], "start_action", held)

    async def relay(index: int) -> list[str]:
        replica.set(replicas[index])
        return await start_approved_actions(grace=timedelta(0))

    async with workers:
        early = asyncio.create_task(relay(0))
        late = asyncio.create_task(relay(1))
        assert await early == [stranded]
        handle = temporal_server.client.get_workflow_handle(f"action-{stranded}")
        assert await asyncio.wait_for(handle.result(), timeout=60) == {"status": "succeeded", "effects": [0]}
        run = (await handle.describe()).run_id
        first_done.set()
        assert await late == [stranded], "the late replica's start is taken as done, not as a failure"
        assert (await handle.describe()).run_id == run, "no second run of action-<id>"
    assert noop_effect == [stranded], "two replicas, one effect"
    assert await _state(stranded) == ("succeeded", [("done", 1)])
