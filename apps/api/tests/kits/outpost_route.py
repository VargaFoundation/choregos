"""A connector at an outpost, in the tests: an outpost that is served, and a run that calls it.

The platform's side is the product's own: the connector is declared, discovered and opened through
the API, and a run calls its tools through the broker. The outpost's side is two things the tests
already have, put together: the scripted daemon of the door's tests (`kits.outpost_daemon.Daemon`:
its keys, its signed requests, the checks the contract asks of a daemon before it opens a call) and
the scriptable `FakeOutpost` of the adapters (what an outpost decides alone and what its server
answers). `serving` runs them as an instance does: it polls the door, hands each call to the fake
outpost and sends back what it made of it. Nothing is written by hand on the way.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.outpost import FakeOutpost
from httpx import AsyncClient
from sqlalchemy import func, select

from . import outpost_daemon as daemon_kit
from . import outposts as kit
from .outpost_daemon import Daemon

ORG = "/api/v1/orgs/varga"
SLUG = daemon_kit.SLUG
ALIAS = "tracker"
CONNECTOR = "tracker"
RUN = "run-outpost-1"
BASE = f"/api/v1/internal/runs/{RUN}/tools"
#: Seconds a connector of these tests waits: long enough for a poll, short enough to wait out.
TIMEOUT_S = 2
#: The connector's timeout for everything a test does not measure: discovery, the calls that are
#: served. A loaded CI runner does not hand a call over, make it and answer it within `TIMEOUT_S`, and
#: a test that is not about the timeout must not fail on one.
SETUP_TIMEOUT_S = 30

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_issues",
        "description": "the issues that match",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "get_issue",
        "description": "one issue",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object"},
    },
    {"name": "create_issue", "description": "opens an issue", "inputSchema": {"type": "object"}},
]


def switch_on(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    """Outposts on, a door that signs, a key to seal a call's arguments under, a daemon that may poll
    as often as a test needs, and a wait that looks often: SQLite has no relay to wake it."""
    from choregos_api.outposts import rendezvous

    daemon_kit.key_wrapper(monkeypatch)
    door = kit.switch_on(monkeypatch)
    kit.configure(monkeypatch, outposts_door_rate_per_minute="0")
    monkeypatch.setattr(rendezvous, "LOOK_S", (0.02, 0.05))
    return door


def server(tools: list[dict[str, Any]] | None = None, **scripted: Any) -> FakeMcpServer:
    return FakeMcpServer(tools=[dict(tool) for tool in (TOOLS if tools is None else tools)], **scripted)


def outpost(served: FakeMcpServer | None = None, alias: str = ALIAS) -> FakeOutpost:
    """An outpost whose policy file has one alias, in front of one server."""
    return FakeOutpost(name=SLUG, endpoints={alias: served or server()})


@contextlib.asynccontextmanager
async def serving(
    client: AsyncClient, daemon: Daemon, fake: FakeOutpost, *, every: float = 0.02
) -> AsyncIterator[None]:
    """An instance of the outpost, alive for the length of the block. While the fake is offline it
    does not poll; a call whose lease the fake loses is taken and never answered."""
    stop = asyncio.Event()

    async def one_round() -> None:
        if fake.offline:
            return
        for call in await daemon.calls(client, endpoints=fake.aliases, capacity=8):
            made = await fake.make(call)
            if made.refusal is not None:
                sent = await daemon.refuse(client, call["id"], made.refusal)
            elif made.answer is not None:
                sent = await daemon.answer(client, call, **made.answer)
            else:
                continue
            # A call the platform stopped waiting for meets `lease_lost`: the door's answer.
            assert sent.status_code in (200, 409), sent.text

    async def instance() -> None:
        while not stop.is_set():
            await one_round()
            await asyncio.sleep(every)

    # The instance has polled once when the block starts: it is online for what the block does
    # first, however slowly the task that keeps it polling gets its turn.
    await one_round()
    task = asyncio.create_task(instance())
    try:
        yield
    finally:
        stop.set()
        await task


async def seen(client: AsyncClient, daemon: Daemon, fake: FakeOutpost) -> None:
    """The instance polls once: it is online from here on, for the aliases its policy file has."""
    response = await daemon.poll(client, endpoints=fake.aliases)
    assert response.status_code == 200, response.text
    assert response.json()["calls"] == []


async def declare(
    client: AsyncClient, name: str = CONNECTOR, *, expect: int = 201, **config: Any
) -> dict[str, Any]:
    body = {"outpost": SLUG, "endpoint": ALIAS, "timeout_s": SETUP_TIMEOUT_S} | config
    declared = await client.post(
        f"{ORG}/connectors", json={"name": name, "type": "mcp-outpost", "config": body}
    )
    assert declared.status_code == expect, declared.text
    return dict(declared.json())


async def open_operation(
    client: AsyncClient, operation: str, policy: str = "allowed", connector: str = CONNECTOR
) -> Any:
    return await client.patch(f"{ORG}/connectors/{connector}/operations/{operation}", json={"policy": policy})


async def agent_and_run(
    client: AsyncClient, project: dict[str, Any], connectors: list[str], run_id: str = RUN
) -> dict[str, str]:
    """An agent whose version names the connectors, and a run of it: the run's token."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    spec = {"mcp_servers": [{"connector": name, "tools": ["*"]} for name in connectors]}
    agent = await client.post(
        f"{ORG}/agents", json={"slug": "coordinator", "display_name": "Coordinator", "spec": spec}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope(orgs="*") as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#1", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=run_id,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="coordinator",
                agent_version=1,
                initiated_by="person:admin@varga.dev",
            )
        )
    token = mint_run_token(run_id, project_slug="billing-api", work_item_key="varga/x#1", ttl_minutes=30)
    return {"Authorization": f"Bearer {token}"}


async def prepared(
    client: AsyncClient,
    project: dict[str, Any],
    fake: FakeOutpost,
    *,
    opened: tuple[str, ...] = ("search_issues", "get_issue"),
    timeout_s: int = SETUP_TIMEOUT_S,
) -> tuple[Daemon, dict[str, str]]:
    """An outpost with one approved instance that polled, a connector declared at it, its tools
    discovered through it and its reads opened, and a run of an agent that names it. The discovery is
    made under `SETUP_TIMEOUT_S`; `timeout_s` is the connector's once it is prepared: a test that
    measures the timeout asks for `TIMEOUT_S`."""
    daemon = await daemon_kit.approved(client)
    await seen(client, daemon, fake)
    await declare(client)
    async with serving(client, daemon, fake):
        discovered = await client.post(f"{ORG}/connectors/{CONNECTOR}/discover")
        assert discovered.status_code == 200, discovered.text
    for operation in opened:
        done = await open_operation(client, operation)
        assert done.status_code == 200, done.text
    if timeout_s != SETUP_TIMEOUT_S:
        await set_timeout(client, timeout_s)
    return daemon, await agent_and_run(client, project, [CONNECTOR])


async def set_timeout(client: AsyncClient, seconds: int, name: str = CONNECTOR) -> None:
    """The connector's timeout from now on, through the route that edits a connector."""
    edited = await client.patch(
        f"{ORG}/connectors/{name}",
        json={"config": {"outpost": SLUG, "endpoint": ALIAS, "timeout_s": seconds}},
    )
    assert edited.status_code == 200, edited.text


# ───────────────────────── what a call left behind ─────────────────────────


async def calls(slug: str = SLUG) -> list[Any]:
    """The rows of the rendezvous, read under the outpost's name."""
    from choregos_api.db.models import OutpostCall

    return sorted(await kit.rows_of(slug, OutpostCall), key=lambda row: (row.created_at, row.id))


async def ledger(provider: str = f"mcp:{CONNECTOR}") -> list[Any]:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = await session.execute(select(CostLedger).where(CostLedger.provider == provider))
        return list(rows.scalars())


async def events(tool: str | None = None) -> list[Any]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = await session.execute(
            select(Event).where(Event.type == "choregos.tool.called").order_by(Event.ts)
        )
        return [event for event in rows.scalars() if tool is None or event.payload.get("tool") == tool]


async def actions() -> int:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return int((await session.execute(select(func.count()).select_from(Action))).scalar_one())


async def journal() -> list[tuple[str, str, str, str, int]]:
    """The network journal: `(host, layer, decision, reason, count)` of each line."""
    from choregos_api.db.models import NetworkDecision
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = (await session.execute(select(NetworkDecision))).scalars()
        return sorted((row.host, row.layer, row.decision, row.reason, row.count) for row in rows)


async def connector_row(name: str = CONNECTOR) -> Any:
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return (await session.execute(select(OrgConnector).where(OrgConnector.name == name))).scalar_one()
