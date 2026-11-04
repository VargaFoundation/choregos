# SPDX-License-Identifier: Apache-2.0
"""An `mcp-gateway` connector is called like an `mcp` one everywhere (S46-09, ADR 0105): a run's read,
a run's write, an approval-gated call, an approved governed write in the workers, and discovery. Each
goes through the organisation's gateway with the signed identity of the call. Mode `required` refuses
direct connectors; `off`, no gateway, or a lost assertion key refuse gateway connectors. Every refusal
happens before anything leaves: no Action, no cost line, no 500, no retry."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import choregos_adapters
import httpx
import pytest
from choregos_adapters.fakes.kubernetes import FakeKubernetesApi
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.mcp_gateway import FakeMcpGateway, GatewayUpstream
from choregos_adapters.plugin_servers import PluginServerRuntime
from choregos_api.secret_store import FakeSecretStore, set_secret_store
from httpx import AsyncClient
from sqlalchemy import func, select

from .test_broker import ORG, TOOLS, _prepare
from .test_plugin_servers import HELLO, INSTALL, SECOND

GATEWAY = f"{ORG}/mcp-gateway"
ENDPOINT = "https://gw.example.test/{server}/mcp"
HOST = "gw.example.test"
RUN = "run-gw-1"
BASE = f"/api/v1/internal/runs/{RUN}/tools"
KEY_PREFIX = "org/mcp-gateway/assertion-key/"


@pytest.fixture(autouse=True)
def store() -> Iterator[FakeSecretStore]:
    from choregos_api.services import mcp_gateway as service

    fake = FakeSecretStore()
    set_secret_store(fake)
    service.clear_token_cache()
    yield fake
    set_secret_store(None)
    service.clear_token_cache()


@pytest.fixture
def direct_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeMcpServer]:
    """The server of the direct `fournisseur` connector (the one `test_broker._prepare` declares)."""
    server = _server(TOOLS, token="cle-fournisseur")
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    monkeypatch.setenv("CONNECTOR_TEST_CLE_FOURNISSEUR", "cle-fournisseur")
    yield server


def _server(tools: list[dict[str, Any]], token: str = "") -> FakeMcpServer:
    return FakeMcpServer(tools=[dict(t) for t in tools], token=token)


async def _put_gateway(client: AsyncClient, mode: str = "available", **over: Any) -> dict[str, Any]:
    body = {
        "mode": mode,
        "endpoint": ENDPOINT,
        "routing": "path",
        "auth": {"type": "jwt_assertion"},
        "audience": "gw",
        **over,
    }
    done = await client.put(GATEWAY, json=body)
    assert done.status_code == 200, done.text
    return dict(done.json())


async def _install_gateway(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, mode: str = "available", **over: Any
) -> FakeMcpGateway:
    """The gateway set, a fake behind it that verifies the assertion, `supplier` an upstream of it."""
    state = await _put_gateway(client, mode, **over)
    fake = FakeMcpGateway(
        auth="jwt_assertion",
        jwks=(await client.get(f"{GATEWAY}/jwks.json")).json(),
        audience="gw",
        issuer=state["assertion"]["issuer"],
        upstreams={
            "supplier": GatewayUpstream(server=_server(TOOLS, token="upstream-key"), token="upstream-key")
        },
    )
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP_GATEWAY", fake)
    return fake


def _upstream(fake: FakeMcpGateway) -> FakeMcpServer:
    server = fake.upstreams["supplier"].server
    assert server is not None
    return server


async def _agent_and_run(client: AsyncClient, project: dict[str, Any], connector: str) -> dict[str, str]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    spec = {"mcp_servers": [{"connector": connector, "tools": ["*"]}]}
    agent = await client.post(
        f"{ORG}/agents", json={"slug": "coordinateur", "display_name": "Coordinateur", "spec": spec}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#1", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id=RUN,
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="coordinateur",
                agent_version=1,
                initiated_by="person:admin@varga.dev",
            )
        )
    token = mint_run_token(RUN, project_slug="billing-api", work_item_key="varga/x#1", ttl_minutes=30)
    return {"Authorization": f"Bearer {token}"}


async def _prepare_gateway(
    client: AsyncClient,
    project: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: str = "available",
) -> tuple[FakeMcpGateway, dict[str, str]]:
    fake = await _install_gateway(client, monkeypatch, mode)
    declared = await client.post(
        f"{ORG}/connectors",
        json={"name": "supplier", "type": "mcp-gateway", "config": {"server": "supplier"}},
    )
    assert declared.status_code == 201, declared.text
    discovered = await client.post(f"{ORG}/connectors/supplier/discover")
    assert discovered.status_code == 200, discovered.text
    for operation, policy in (
        ("suivi_commande", "allowed"),
        ("commander_poste", "allowed"),
        ("stock", "approval"),
    ):
        opened = await client.patch(
            f"{ORG}/connectors/supplier/operations/{operation}", json={"policy": policy}
        )
        assert opened.status_code == 200, opened.text
    return fake, await _agent_and_run(client, project, "supplier")


async def _prepare_direct(client: AsyncClient, project: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """A direct `fournisseur` connector (discovered, opened) and a run of its agent."""
    return await _prepare(client, project, ["*"])


async def _ledger(provider: str) -> int:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        count = await session.execute(
            select(func.count()).select_from(CostLedger).where(CostLedger.provider == provider)
        )
        return int(count.scalar_one())


async def _actions() -> int:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return int((await session.execute(select(func.count()).select_from(Action))).scalar_one())


async def _connector_status(name: str) -> str:
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        row = (await session.execute(select(OrgConnector).where(OrgConnector.name == name))).scalar_one()
        return str(row.status)


def _lose_the_assertion_key(store: FakeSecretStore) -> None:
    for key in [k for k in store.values if k[1].startswith(KEY_PREFIX)]:
        del store.values[key]


async def _call_effect(project_id: str, operation: str, *, approved_by: list[str] | None = None) -> Any:
    from choregos_api.db.models import Action, Project, Run
    from choregos_api.db.session import session_scope
    from choregos_api.effects import EffectContext, effect

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        run = await session.get(Run, RUN)
        assert project is not None and run is not None
        action = Action(
            org_id=project.org_id,
            project_id=project_id,
            run_id=run.id,
            work_item_id=run.work_item_id,
            origin="tool",
            kind=f"supplier.{operation}",
            title="x",
            params={},
            effects=[],
            proposed_by={},
            approval={},
            decisions=[{"by": by, "decision": "approve"} for by in (approved_by or [])],
            status="approved",
        )
        session.add(action)
        await session.flush()
        call = {"connector": "supplier", "operation": operation, "arguments": {}}
        done = await effect("connector.call")(EffectContext(session, action, project), call)
        return action.id, done


# ───────────────────────────── declaration and discovery ─────────────────────────────


async def test_a_gateway_connector_needs_a_gateway(client: AsyncClient, admin: str) -> None:
    refused = await client.post(
        f"{ORG}/connectors",
        json={"name": "supplier", "type": "mcp-gateway", "config": {"server": "supplier"}},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "gateway_not_configured"
    assert (await client.get(f"{ORG}/connectors/supplier")).status_code == 404, "nothing is created"


async def test_discovery_goes_through_the_gateway_and_tools_are_born_closed(
    client: AsyncClient, admin: str, store: FakeSecretStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = await _install_gateway(client, monkeypatch)
    declared = await client.post(
        f"{ORG}/connectors",
        json={"name": "supplier", "type": "mcp-gateway", "config": {"server": "supplier"}},
    )
    assert declared.status_code == 201, declared.text
    found = await client.post(f"{ORG}/connectors/supplier/discover")
    assert found.status_code == 200, found.text
    assert len(found.json()["added"]) == 3
    operations = (await client.get(f"{ORG}/connectors/supplier")).json()["operations"]
    assert {op["policy"] for op in operations} == {"forbidden"}
    claims = [c.claims for c in fake.calls if c.claims]
    assert claims and all(
        c["purpose"] == "discovery" and c["sub"] == "person:admin@varga.dev" for c in claims
    )
    upstream = _upstream(fake)
    assert upstream.received and {h["authorization"] for _, h, _ in upstream.received} == {
        "Bearer upstream-key"
    }
    assert "upstream-key" not in {value[0] for value in store.values.values()}


# ───────────────────────────── a run's calls ─────────────────────────────


async def test_a_runs_read_reaches_the_server_through_the_gateway_with_its_identity(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import CostLedger, Event
    from choregos_api.db.session import session_scope

    fake, headers = await _prepare_gateway(client, project, monkeypatch)
    answer = await client.post(f"{BASE}/supplier__suivi_commande", headers=headers, json={"numero": "42"})
    assert answer.status_code == 200 and answer.json()["status_code"] == 200, answer.text
    assert _upstream(fake).calls == [("suivi_commande", {"numero": "42"})]
    (call,) = [c for c in fake.calls if c.method == "tools/call"]
    assert call.claims is not None
    assert {
        k: call.claims.get(k) for k in ("run", "work_item", "agent", "initiator", "tool", "connector")
    } == {
        "run": RUN,
        "work_item": "varga/x#1",
        "agent": "coordinateur@1",
        "initiator": "person:admin@varga.dev",
        "tool": "suivi_commande",
        "connector": "supplier",
    }
    async with session_scope() as session:
        ledger = (await session.execute(select(CostLedger).where(CostLedger.run_id == RUN))).scalars().all()
        events = (
            (await session.execute(select(Event).where(Event.type == "choregos.tool.called"))).scalars().all()
        )
    assert [row.provider for row in ledger] == ["mcp:supplier"]
    assert [e.subject for e in events] == [RUN]


async def test_the_run_token_never_reaches_the_gateway(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, headers = await _prepare_gateway(client, project, monkeypatch)
    run_token = headers["Authorization"].removeprefix("Bearer ")
    answer = await client.post(f"{BASE}/supplier__suivi_commande", headers=headers, json={"numero": "42"})
    assert answer.json()["status_code"] == 200, answer.text
    assert fake.calls
    for call in fake.calls:
        assert run_token not in json.dumps(call.headers), call.method


async def test_an_approved_write_reaches_the_gateway_with_its_approvers(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, _headers = await _prepare_gateway(client, project, monkeypatch)
    action_id, _done = await _call_effect(project["id"], "commander_poste", approved_by=["rh@varga.dev"])
    (call,) = [c for c in fake.calls if c.method == "tools/call"]
    assert call.claims is not None
    assert call.claims["action"] == action_id
    assert call.claims["approved_by"] == ["rh@varga.dev"]
    assert call.claims["purpose"] == "action" and call.claims["run"] == RUN


# ───────────────────────────── the modes ─────────────────────────────


async def test_required_mode_refuses_direct_connectors_at_declaration_and_at_call(
    client: AsyncClient,
    project: dict[str, Any],
    direct_server: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, headers = await _prepare_direct(client, project)
    await _install_gateway(client, monkeypatch, "required")
    declared = await client.post(
        f"{ORG}/connectors",
        json={"name": "autre", "type": "mcp", "config": {"url": "https://autre.test/mcp"}},
    )
    assert declared.status_code == 409, declared.text
    assert declared.json()["code"] == "gateway_required"
    read = await client.post(f"{base}/fournisseur__suivi_commande", headers=headers, json={"numero": "1"})
    assert read.status_code == 200 and read.json()["status_code"] == 403, read.text
    assert read.json()["result"]["type"] == "network_policy"
    assert read.json()["result"]["code"] == "gateway_required"
    assert not [name for name, _args in direct_server.calls], "nothing reached the server"
    assert await _ledger("mcp:fournisseur") == 0


async def test_required_mode_refuses_a_direct_write_and_an_approval_without_an_action(
    client: AsyncClient,
    project: dict[str, Any],
    direct_server: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, headers = await _prepare_direct(client, project)  # commander_poste is under approval
    await _install_gateway(client, monkeypatch, "required")
    gated = await client.post(f"{base}/fournisseur__commander_poste", headers=headers, json={})
    assert gated.status_code == 403, gated.text
    assert gated.json()["code"] == "gateway_required"
    opened = await client.patch(
        f"{ORG}/connectors/fournisseur/operations/commander_poste", json={"policy": "allowed"}
    )
    assert opened.status_code == 200, opened.text
    write = await client.post(f"{base}/fournisseur__commander_poste", headers=headers, json={})
    assert write.status_code == 200 and write.json()["status_code"] == 403, write.text
    assert write.json()["result"]["code"] == "gateway_required"
    assert await _actions() == 0
    assert direct_server.calls == []
    assert await _ledger("mcp:fournisseur") == 0


async def test_off_mode_refuses_gateway_connectors_without_calling(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake, headers = await _prepare_gateway(client, project, monkeypatch)
    before = len(fake.calls)
    await _put_gateway(client, "off")
    read = await client.post(f"{BASE}/supplier__suivi_commande", headers=headers, json={"numero": "1"})
    assert read.json()["status_code"] == 403, read.text
    assert read.json()["result"]["code"] == "gateway_not_configured"
    write = await client.post(f"{BASE}/supplier__commander_poste", headers=headers, json={})
    assert write.json()["status_code"] == 403 and write.json()["result"]["code"] == "gateway_not_configured"
    assert await _actions() == 0
    discovered = await client.post(f"{ORG}/connectors/supplier/discover")
    assert discovered.status_code == 409 and discovered.json()["code"] == "gateway_not_configured"
    assert await _connector_status("supplier") != "error"
    assert len(fake.calls) == before, "nothing reached the gateway"


async def test_a_lost_assertion_key_is_a_refusal_not_a_crash(
    client: AsyncClient, project: dict[str, Any], store: FakeSecretStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.effects import EffectRefused

    fake, headers = await _prepare_gateway(client, project, monkeypatch)
    before = len(fake.calls)
    _lose_the_assertion_key(store)
    read = await client.post(f"{BASE}/supplier__suivi_commande", headers=headers, json={"numero": "1"})
    assert read.status_code == 200 and read.json()["status_code"] == 403, read.text
    assert read.json()["result"]["code"] == "gateway_assertion_key_missing"
    discovered = await client.post(f"{ORG}/connectors/supplier/discover")
    assert discovered.status_code == 409 and discovered.json()["code"] == "gateway_assertion_key_missing"
    assert await _connector_status("supplier") != "error"
    with pytest.raises(EffectRefused, match="assertion key"):
        await _call_effect(project["id"], "commander_poste")
    assert len(fake.calls) == before


async def test_the_network_bounds_judge_the_gateway_host(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import NetworkDecision
    from choregos_api.db.session import session_scope

    fake, headers = await _prepare_gateway(client, project, monkeypatch)
    ceiling = await client.put(
        f"{ORG}/policy-ceiling", json={"ceiling": {"network": {"deny_domains": [HOST]}}}
    )
    assert ceiling.status_code == 200, ceiling.text
    before = len(fake.calls)
    read = await client.post(f"{BASE}/supplier__suivi_commande", headers=headers, json={"numero": "1"})
    assert read.json()["status_code"] == 403 and read.json()["result"]["type"] == "network_policy", read.text
    assert len(fake.calls) == before
    async with session_scope() as session:
        rows = (await session.execute(select(NetworkDecision))).scalars().all()
    assert [(r.host, r.layer, r.decision) for r in rows] == [(HOST, "broker", "denied")]


async def test_the_connector_timeout_wins_over_the_gateways(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope
    from choregos_api.services.mcp_gateway import connector_client

    await _install_gateway(client, monkeypatch, timeout_s=30)
    for name, config in (("slow", {"server": "supplier", "timeout_s": 5}), ("plain", {"server": "supplier"})):
        made = await client.post(
            f"{ORG}/connectors", json={"name": name, "type": "mcp-gateway", "config": config}
        )
        assert made.status_code == 201, made.text

    async def identity() -> Any:
        from choregos_api.services.mcp_gateway_identity import CallIdentity

        return CallIdentity(org="varga", org_id="x", subject="person:admin@varga.dev", purpose="test")

    async with session_scope() as session:
        timeouts = {}
        for name in ("slow", "plain"):
            row = (await session.execute(select(OrgConnector).where(OrgConnector.name == name))).scalar_one()
            timeouts[name] = (await connector_client(session, row, identity))._timeout
    assert timeouts == {"slow": 5.0, "plain": 30.0}


async def test_an_effect_refused_by_the_gateway_route_is_final(
    client: AsyncClient,
    project: dict[str, Any],
    direct_server: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effects import EffectContext, EffectRefused, effect

    await _prepare_direct(client, project)
    await _install_gateway(client, monkeypatch, "required")
    async with session_scope() as session:
        found = await session.get(Project, project["id"])
        assert found is not None
        action = Action(
            org_id=found.org_id,
            project_id=found.id,
            origin="tool",
            kind="x",
            title="x",
            params={},
            effects=[],
            proposed_by={},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        call = {"connector": "fournisseur", "operation": "suivi_commande", "arguments": {"numero": "1"}}
        with pytest.raises(EffectRefused, match="only through its MCP gateway"):
            await effect("connector.call")(EffectContext(session, action, found), call)
    assert direct_server.calls == []


# ───────────────────────────── a plugin's server ─────────────────────────────


async def test_required_mode_exempts_plugin_servers(
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin's server is deployed by the platform (ADR 0096): `required` does not refuse it."""
    from choregos_api.services.plugin_servers import use_plugin_runtime, use_plugin_transport
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    from .agent_scopes_kit import add_member, as_user
    from .test_plugin_servers import _mcp_server

    root = tmp_path / "plugins"
    shutil.copytree(HELLO, root / "hello" / "1.0.0")
    use_plugin_supply(OfflineDirectory(root))
    use_plugin_runtime(PluginServerRuntime(FakeKubernetesApi(), "choregos-plugins"))
    try:
        asked = await client.post(f"{ORG}/plugins/install", json=INSTALL)
        assert asked.status_code == 202, asked.text
        await add_member(client, SECOND, "org_admin")
        await as_user(client, SECOND)
        approved = await client.post(f"{ORG}/plugin-approvals/{asked.json()['approval_id']}/approve")
        assert approved.status_code == 200, approved.text
        await as_user(client, "admin@varga.dev")
        seen: list[httpx.Request] = []
        use_plugin_transport(_mcp_server(["say_hello", "post_greeting"], seen))
        fake = await _install_gateway(client, monkeypatch, "required")
        found = await client.post(f"{ORG}/connectors/hello/discover")
        assert found.status_code == 200, found.text
        headers = await _agent_and_run(client, project, "hello")
        seen.clear()
        read = await client.post(f"{BASE}/hello__say_hello", headers=headers, json={})
        assert read.status_code == 200 and read.json()["status_code"] == 200, read.text
        assert [json.loads(r.content).get("method") for r in seen if r.content][-1] == "tools/call"
        assert fake.calls == [], "the gateway recorded no call"
    finally:
        use_plugin_runtime(None)
        use_plugin_transport(None)
        use_plugin_supply(None)
