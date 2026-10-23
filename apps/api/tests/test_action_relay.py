"""The relay of approved actions outside its fakes (ADR 0085, S40-19b).

`test_action_outbox.py` proves what one relay pass does against `FakeTemporal`. Here: the pass runs
because the API's lifespan starts it, and it holds against a real Temporal server — refused while
the server is unreachable, one run of `action-<id>` however many passes come. In CI's second pass
(`CHOREGOS_TEST_SUITE_SUR_POSTGRES=1`) the same tests run on a migrated PostgreSQL under RLS.
"""

from __future__ import annotations

import asyncio
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
def noop_effect() -> Iterator[None]:
    from choregos_api import effets

    async def noop(_ctx: Any, _params: dict[str, Any]) -> dict[str, Any]:
        return {}

    effets.declarer_un_effet("test.noop", noop)
    yield
    effets.reinitialiser()


@pytest.fixture
async def temporal_server() -> AsyncIterator[Any]:
    """A real Temporal dev server (the `temporal` CLI's, downloaded by the SDK on first use)."""
    from temporalio.testing import WorkflowEnvironment

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
