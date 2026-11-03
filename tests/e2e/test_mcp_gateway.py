"""An agent's read and governed writes reach a server through the organisation's MCP gateway (S46-11).

End to end, with the real Temporal test server and the platform's workers (in memory). The
organisation's gateway, in JWT assertion mode and `required` mode, fronts a fake supplier server:
- a run reads through the gateway;
- an allowed write is a governed action in the workers, through the gateway, once even replayed;
- an approval-gated write waits for a person and carries that person's name.

Each request carries an identity verifiable against the JWKS. The upstream credential exists only
in the gateway; the run token never leaves Choregos. Nothing here patches the connector client:
the calls take the real paths (broker, effects, discovery) of `services/plugin_servers`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

ORG = "/api/v1/orgs/varga"
GATEWAY = f"{ORG}/mcp-gateway"
RUN = "run-gw-1"
BASE = f"/api/v1/internal/runs/{RUN}/tools"
UPSTREAM_KEY = "upstream-key"


def _server(tools: list[dict[str, Any]], token: str, gestures: dict[str, Any]) -> Any:
    from choregos_adapters.fakes.mcp import FakeMcpServer

    return FakeMcpServer(outils=tools, jeton=token, gestes=gestures)


def _received(server: Any) -> list[tuple[str, dict[str, str], dict[str, Any]]]:
    return list(server.recues)


def _called(server: Any) -> list[tuple[str, dict[str, Any]]]:
    return list(server.appels)


def _order_pc(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"serial": f"SN-{arguments['model']}", "order": "C-1"}


def _order_monitor(arguments: dict[str, Any]) -> dict[str, Any]:
    return {"serial": f"MON-{arguments['model']}", "order": "C-2"}


class Bench:
    def __init__(self, gateway: Any, headers: dict[str, str], issuer: str, store: Any) -> None:
        self.gateway = gateway
        self.headers = headers
        self.issuer = issuer
        self.store = store

    @property
    def upstream(self) -> Any:
        return self.gateway.upstreams["supplier"].server

    @property
    def run_token(self) -> str:
        return self.headers["Authorization"].removeprefix("Bearer ")

    def tool_calls(self) -> list[Any]:
        return [call for call in self.gateway.calls if call.method == "tools/call"]


@pytest.fixture
def secret_store() -> Iterator[Any]:
    from choregos_api.secret_store import FakeSecretStore, set_secret_store

    store = FakeSecretStore()
    set_secret_store(store)
    yield store
    set_secret_store(None)


async def _prepare(platform: Platform, monkeypatch: pytest.MonkeyPatch, store: Any) -> Bench:
    import choregos_adapters
    from choregos_adapters.fakes.mcp_gateway import FakeMcpGateway, GatewayUpstream
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    client = platform.client
    await login(client)
    put = await client.put(
        GATEWAY,
        json={
            "mode": "required",
            "endpoint": "https://gw.example.test/{server}/mcp",
            "routing": "path",
            "auth": {"type": "jwt_assertion"},
            "audience": "choregos-e2e",
            "allow_egress": True,
        },
    )
    assert put.status_code == 200, put.text
    issuer = put.json()["assertion"]["issuer"]
    jwks = (await client.get(f"{GATEWAY}/jwks.json")).json()
    tools = [
        {"name": "order_pc", "description": "orders a PC", "inputSchema": {"type": "object"}},
        {"name": "order_status", "description": "where the order stands", "inputSchema": {"type": "object"},
         "annotations": {"readOnlyHint": True}},
        {"name": "order_monitor", "description": "orders a monitor", "inputSchema": {"type": "object"}},
    ]  # fmt: skip
    server = _server(tools, UPSTREAM_KEY, {"order_pc": _order_pc, "order_monitor": _order_monitor})
    gateway = FakeMcpGateway(
        auth="jwt_assertion",
        jwks=jwks,
        audience="choregos-e2e",
        issuer=issuer,
        upstreams={"supplier": GatewayUpstream(server=server, token=UPSTREAM_KEY)},
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", gateway)
    declared = await client.post(
        f"{ORG}/connectors",
        json={"name": "supplier", "type": "mcp-gateway", "config": {"server": "supplier"}},
    )
    assert declared.status_code == 201, declared.text
    assert (await client.post(f"{ORG}/connectors/supplier/discover")).status_code == 200
    for operation, policy in (
        ("order_status", "allowed"),
        ("order_pc", "allowed"),
        ("order_monitor", "approval"),
    ):
        opened = await client.patch(
            f"{ORG}/connectors/supplier/operations/{operation}", json={"policy": policy}
        )
        assert opened.status_code == 200, opened.text
    spec = {"mcp_servers": [{"connector": "supplier", "tools": ["*"]}]}
    agent = await client.post(f"{ORG}/agents", json={"slug": "buyer", "display_name": "Buyer", "spec": spec})
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(
            project_id=platform.project_id, tracker_key="varga/billing-api#11", title="A PC", state="ready"
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=RUN,
                work_item_id=item.id,
                project_id=platform.project_id,
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="buyer",
                agent_version=1,
                initiated_by="person:admin@varga.dev",
            )
        )
    token = mint_run_token(
        RUN,
        project_slug=platform.project_slug,
        work_item_key="varga/billing-api#11",
        ttl_minutes=30,
    )
    return Bench(gateway, {"Authorization": f"Bearer {token}"}, issuer, store)


async def _wait_for_action(client: Any, project_id: str, action_id: str) -> None:
    for _ in range(100):
        action = (await client.get(f"/api/v1/projects/{project_id}/actions/{action_id}")).json()
        if action["status"] in {"succeeded", "failed", "refused"}:
            return
        await asyncio.sleep(0.1)


async def test_a_read_goes_through_the_gateway_with_the_runs_identity(
    platform: Platform, monkeypatch: pytest.MonkeyPatch, secret_store: Any
) -> None:
    bench = await _prepare(platform, monkeypatch, secret_store)
    answer = await platform.client.post(f"{BASE}/supplier__order_status", headers=bench.headers, json={})
    assert answer.status_code == 200, answer.text
    assert answer.json()["status_code"] == 200, answer.text
    (call,) = bench.tool_calls()
    assert call.claims is not None
    assert {k: call.claims.get(k) for k in ("run", "work_item", "agent", "initiator", "tool", "iss")} == {
        "run": RUN,
        "work_item": "varga/billing-api#11",
        "agent": "buyer@1",
        "initiator": "person:admin@varga.dev",
        "tool": "order_status",
        "iss": bench.issuer,
    }


async def test_an_allowed_write_is_one_governed_action_through_the_gateway(
    platform: Platform, worker: Any, monkeypatch: pytest.MonkeyPatch, secret_store: Any
) -> None:
    bench = await _prepare(platform, monkeypatch, secret_store)
    client = platform.client
    async with worker():
        first = await client.post(f"{BASE}/supplier__order_pc", headers=bench.headers, json={"model": "x14"})
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["status_code"] == 200, body
        assert body["result"]["structuredContent"]["serial"] == "SN-x14"
        action_id = body["result"]["action"]
        assert _called(bench.upstream) == [("order_pc", {"model": "x14"})]

        replayed = (
            await client.post(f"{BASE}/supplier__order_pc", headers=bench.headers, json={"model": "x14"})
        ).json()
        assert replayed["result"]["action"] == action_id
        assert _called(bench.upstream) == [("order_pc", {"model": "x14"})], "replayed, it writes once"

    claims = [call.claims for call in bench.tool_calls() if call.claims]
    assert claims and all(c["purpose"] == "action" and c["approved_by"] == ["policy"] for c in claims)
    assert {c["action"] for c in claims} == {action_id}


async def test_an_approval_gated_write_waits_for_a_person_and_carries_their_name(
    platform: Platform, worker: Any, monkeypatch: pytest.MonkeyPatch, secret_store: Any
) -> None:
    bench = await _prepare(platform, monkeypatch, secret_store)
    client = platform.client
    waiting = await client.post(
        f"{BASE}/supplier__order_monitor", headers=bench.headers, json={"model": "m27"}
    )
    assert waiting.status_code == 200 and waiting.json()["status_code"] == 202, waiting.text
    action_id = waiting.json()["result"]["action"]
    assert _called(bench.upstream) == [], "nothing reaches the server before a person decides"
    assert bench.tool_calls() == []

    decision = f"/api/v1/projects/{platform.project_id}/actions/{action_id}/decision"
    own = await client.post(decision, json={"decision": "approve"})
    assert own.status_code == 422 and "separation of duties" in own.text
    member = await client.post(f"{ORG}/members", json={"email": "rh@varga.dev", "role": "project_owner"})
    assert member.status_code in {200, 201}, member.text
    await login(client, "rh@varga.dev")
    async with worker():
        approved = await client.post(decision, json={"decision": "approve"})
        assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text
        await _wait_for_action(client, platform.project_id, action_id)

    action = (await client.get(f"/api/v1/projects/{platform.project_id}/actions/{action_id}")).json()
    assert action["status"] == "succeeded", action
    assert _called(bench.upstream) == [("order_monitor", {"model": "m27"})]
    (call,) = bench.tool_calls()
    assert call.claims is not None
    assert call.claims["approved_by"] == ["rh@varga.dev"]
    assert call.claims["action"] == action_id


async def test_nothing_of_choregos_s_reaches_the_upstream_and_no_upstream_key_is_in_choregos(
    platform: Platform, worker: Any, monkeypatch: pytest.MonkeyPatch, secret_store: Any
) -> None:
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    bench = await _prepare(platform, monkeypatch, secret_store)
    client = platform.client
    async with worker():
        read = await client.post(f"{BASE}/supplier__order_status", headers=bench.headers, json={})
        assert read.json()["status_code"] == 200, read.text
        write = await client.post(f"{BASE}/supplier__order_pc", headers=bench.headers, json={"model": "x14"})
        assert write.json()["status_code"] == 200, write.text

    received = _received(bench.upstream)
    assert received, "the upstream was called"
    assert {headers["authorization"] for _, headers, _ in received} == {f"Bearer {UPSTREAM_KEY}"}
    assert bench.run_token not in json.dumps(received)
    assert bench.gateway.calls
    for call in bench.gateway.calls:
        assert bench.run_token not in json.dumps(call.headers), call.method
    assert UPSTREAM_KEY not in {value[0] for value in bench.store.values.values()}
    async with session_scope() as session:
        direct = (
            (
                await session.execute(
                    select(OrgConnector).where(
                        OrgConnector.type == "mcp", OrgConnector.plugin_server.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert direct == [], "the organisation has no direct MCP connector"
