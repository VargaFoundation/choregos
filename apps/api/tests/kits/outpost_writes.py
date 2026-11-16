"""A write through an outpost, in the tests: a connector whose writes are opened under the rule,
and a governed action played as the `ActionWorkflow` plays one.

It stands on the kit of the route (`kits.outpost_route`): the platform's side is the product's own,
through the API; the outpost's side is a scripted daemon at the real door in front of the
scriptable `FakeOutpost`. What is added here is what a write needs: a server whose tools include
writes (one of them takes the platform's idempotency key), the statements an administrator makes
when opening them, an approved action whose effects call them, and the activities that play it.
"""

from __future__ import annotations

from typing import Any

from choregos_adapters.fakes.outpost import FakeOutpost
from httpx import AsyncClient
from sqlalchemy import select

from . import outpost_daemon as daemon_kit
from . import outpost_route as route
from .outpost_daemon import Daemon
from .outpost_route import ALIAS, CONNECTOR, ORG, RUN

#: Seconds the connector of these tests waits once it is prepared: a lost lease is in doubt after this
#: long, and a call nobody claims is offline after it. Long enough for a loaded CI runner to hand a
#: call over to the scripted instance, short enough that a test that waits for it stays quick. The
#: discovery itself is made under the route kit's `SETUP_TIMEOUT_S`.
TIMEOUT_S = 4
READS = ("search_issues", "get_issue")
#: An in-doubt outcome is accepted for the operation, explicitly.
ACCEPTED = {"in_doubt_accepted": True}
#: The operation takes the platform's idempotency key.
KEYED = {"platform_arguments": ["action_key"]}

_TITLE = {"type": "object", "properties": {"title": {"type": "string"}}}
TOOLS: list[dict[str, Any]] = [
    *route.TOOLS[:2],
    {"name": "create_issue", "description": "opens an issue", "inputSchema": _TITLE},
    {"name": "update_issue", "description": "changes an issue", "inputSchema": _TITLE},
    {
        "name": "delete_issue",
        "description": "deletes an issue",
        "inputSchema": {"type": "object", "properties": {"key": {"type": "string"}}},
    },
    {
        "name": "create_ticket",
        "description": "opens a ticket, once per key",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "action_key": {"type": "string"},
                "work_item_key": {"type": "string"},
            },
        },
    },
]
STRUCTURED = {
    "create_issue": {"key": "OPS-7"},
    "update_issue": {"key": "OPS-7", "changed": True},
    "delete_issue": {"deleted": True},
    "create_ticket": {"ticket": "T-1"},
}


def outpost(**scripted: Any) -> tuple[Any, FakeOutpost]:
    """A server with writes behind an outpost whose policy file allows them."""
    served = route.server(TOOLS, **({"structured": dict(STRUCTURED)} | scripted))
    fake = route.outpost(served)
    fake.writes_allowed.add(ALIAS)
    return served, fake


async def state(client: AsyncClient, operation: str, **body: Any) -> Any:
    """What an administrator sends to `updateConnectorOperation`."""
    return await client.patch(f"{ORG}/connectors/{CONNECTOR}/operations/{operation}", json=body)


async def operation_of(client: AsyncClient, name: str) -> dict[str, Any]:
    connector = (await client.get(f"{ORG}/connectors/{CONNECTOR}")).json()
    return next(dict(op) for op in connector["operations"] if op["name"] == name)


async def prepared(
    client: AsyncClient,
    project: dict[str, Any],
    fake: FakeOutpost,
    *,
    opened: dict[str, dict[str, Any]] | None = None,
    policy: str = "allowed",
) -> tuple[Daemon, dict[str, str]]:
    """An outpost with one approved instance that polled, a connector declared at it, its tools
    discovered and its reads opened, each write of `opened` opened under the statement given for it,
    and a run of an agent that names the connector."""
    daemon = await daemon_kit.approved(client)
    await route.seen(client, daemon, fake)
    await route.declare(client)
    async with route.serving(client, daemon, fake):
        discovered = await client.post(f"{ORG}/connectors/{CONNECTOR}/discover")
        assert discovered.status_code == 200, discovered.text
    await route.set_timeout(client, TIMEOUT_S)
    for name in READS:
        assert (await route.open_operation(client, name)).status_code == 200
    for name, statement in (opened or {}).items():
        done = await state(client, name, policy=policy, **statement)
        assert done.status_code == 200, done.text
    return daemon, await route.agent_and_run(client, project, [CONNECTOR])


def call(
    operation: str, arguments: dict[str, Any], compensate: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A `connector.call` effect of the connector at the outpost, as the broker builds one."""
    effect: dict[str, Any] = {
        "effect": "connector.call",
        "with": {"connector": CONNECTOR, "operation": operation, "arguments": arguments},
    }
    if compensate is not None:
        effect["compensate"] = {"effect": "connector.call", "with": {"connector": CONNECTOR, **compensate}}
    return effect


async def action(project_id: str, effects: list[dict[str, Any]], status: str = "approved") -> str:
    """An action of the run, approved: what the `ActionWorkflow` is started for."""
    from choregos_api.db.models import Action, Project, Run
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        project = await session.get(Project, project_id)
        run = await session.get(Run, RUN)
        assert project is not None and run is not None
        row = Action(
            org_id=project.org_id,
            project_id=project_id,
            run_id=run.id,
            work_item_id=run.work_item_id,
            origin="tool",
            kind=f"{CONNECTOR}.write",
            title="A write at the outpost",
            params={},
            effects=effects,
            proposed_by={"kind": "agent", "id": "agent:coordinator", "run_id": run.id},
            approval={},
            decisions=[{"by": "ops@varga.dev", "decision": "approve"}],
            status=status,
        )
        session.add(row)
        await session.flush()
        return str(row.id)


async def play(action_id: str, position: int = 0) -> dict[str, Any]:
    """One attempt of an effect, as the workflow's activity makes it."""
    from choregos_orchestrator.activities.actions import execute_effect

    return dict(await execute_effect({"action_id": action_id, "position": position}))


async def attempt_that_dies(action_id: str, position: int = 0) -> dict[str, Any]:
    """An attempt whose worker is killed once the outpost answered and before the answer is
    recorded: the effect is played under its key, as the activity plays it, and nothing is kept."""
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effects import EffectContext, effect

    async with session_scope(orgs="*") as session:
        row = await session.get(Action, action_id)
        assert row is not None
        project = await session.get(Project, row.project_id)
        assert project is not None
        spec = row.effects[position]
        context = EffectContext(session, row, project, key=f"{action_id}:{position}")
        return dict(await effect(str(spec["effect"]))(context, dict(spec["with"])))


async def journal(action_id: str) -> list[Any]:
    """The effects of an action, by position."""
    from choregos_api.db.models import ActionEffect
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = await session.execute(
            select(ActionEffect).where(ActionEffect.action_id == action_id).order_by(ActionEffect.position)
        )
        return list(rows.scalars())


async def action_row(action_id: str) -> Any:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        row = await session.get(Action, action_id)
        assert row is not None
        return row


async def deliveries() -> list[Any]:
    """The tool calls placed at the rendezvous, oldest first: one row per delivery of a write."""
    return [row for row in await route.calls() if row.kind == "call_tool"]
