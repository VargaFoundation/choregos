# SPDX-License-Identifier: Apache-2.0
"""The MCP gateway's host is never a run's, and its openings leave with it (S46-19, ADR 0105).

Each guard is proved by a test that fails without it: the refusal of a `runners` rule at the three
doors (create, request, approve) and at the governed add, the per-organisation deny in the effective
network (not the instance deny, which would also bound the platform's own calls), and the revocation of
the rules the gateway itself created when it moves or is deleted."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_api.errors import ApiError
from choregos_api.secret_store import FakeSecretStore, set_secret_store
from httpx import AsyncClient
from sqlalchemy import select

from .egress_support import ORG, rules_in_db

URL = f"{ORG}/mcp-gateway"
HOST = "gw.example.test"
ENDPOINT = "https://gw.example.test/{server}/mcp"


@pytest.fixture(autouse=True)
def isolated() -> Iterator[FakeSecretStore]:
    from choregos_api.services import mcp_gateway as service

    store = FakeSecretStore()
    set_secret_store(store)
    service.clear_token_cache()
    yield store
    set_secret_store(None)
    service.clear_token_cache()


@pytest.fixture
def enforce(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()


def _body(endpoint: str = ENDPOINT, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "available",
        "endpoint": endpoint,
        "routing": "path",
        "auth": {"type": "bearer"},
        "secret": "s3cr3t-credential-value",
    }
    body.update(over)
    return body


async def _set_gateway(client: AsyncClient, endpoint: str = ENDPOINT, **over: Any) -> None:
    done = await client.put(URL, json=_body(endpoint, **over))
    assert done.status_code == 200, done.text


async def _organisation() -> Any:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        found = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        session.expunge(found)
        return found


async def _seed_rule(host: str, used_by: list[str], status: str = "allowed", origin: str = "manual") -> str:
    from choregos_api.db.models import OrgEgressRule
    from choregos_api.db.session import session_scope

    org = await _organisation()
    async with session_scope() as session:
        row = OrgEgressRule(
            org_id=org.id,
            host=host,
            ports=[443],
            used_by=used_by,
            status=status,
            reason="seeded",
            origin=origin,
            requested_by="admin@varga.dev",
        )
        session.add(row)
        await session.flush()
        return str(row.id)


async def _audit(action: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars()
        return [dict(r.payload) for r in rows]


def _rule(host: str, used_by: list[str]) -> dict[str, Any]:
    return {"host": host, "ports": [443], "used_by": used_by, "reason": "because"}


async def test_a_runners_rule_for_the_gateway_host_is_refused(client: AsyncClient, admin: str) -> None:
    await _set_gateway(client)
    refused = await client.post(f"{ORG}/egress/rules", json=_rule(HOST, ["runners"]))
    assert refused.status_code == 422, refused.text
    assert "0105" in refused.text
    ok = await client.post(f"{ORG}/egress/rules", json=_rule(HOST, ["api"]))
    assert ok.status_code == 201, ok.text


async def test_a_request_and_an_approval_for_runners_are_refused_too(client: AsyncClient, admin: str) -> None:
    await _set_gateway(client)
    refused = await client.post(f"{ORG}/egress/requests", json=_rule(HOST, ["runners"]))
    assert refused.status_code == 422, refused.text
    waiting = await _seed_rule(HOST, ["runners"], status="requested")
    approved = await client.post(f"{ORG}/egress/requests/{waiting}/approve", json={})
    assert approved.status_code == 422, approved.text
    assert await rules_in_db("allowed") == []


async def test_a_governed_add_cannot_open_the_gateway_for_runners(client: AsyncClient, admin: str) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import Principal
    from choregos_api.services.egress import Target, allow_hosts

    await _set_gateway(client)
    organisation = await _organisation()
    principal = Principal(user_id="u", email="admin@varga.dev", org_roles={"varga": "org_admin"})
    async with session_scope() as session:
        with pytest.raises(ApiError) as raised:
            await allow_hosts(
                session,
                principal,
                organisation,
                [Target(HOST, 443, "runners")],
                origin="x",
                reason="y",
            )
    assert raised.value.status_code == 422


async def test_runs_never_reach_the_gateway_host(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.egress import org_runner_hosts
    from choregos_api.services.network import project_effective_network

    await _seed_rule(HOST, ["runners"])
    organisation = await _organisation()
    async with session_scope() as session:
        assert HOST in await org_runner_hosts(session, organisation.id)
    await _set_gateway(client)
    async with session_scope() as session:
        assert HOST not in await org_runner_hosts(session, organisation.id)
        row = await session.get(Project, project["id"])
        assert row is not None
        effective, _ = await project_effective_network(session, row)
    assert HOST in effective.deny
    assert HOST not in effective.reachable


async def test_an_agent_version_naming_the_gateway_host_is_refused(client: AsyncClient, admin: str) -> None:
    await _set_gateway(client)
    spec = {"instructions": "You are an architect.", "model": "m", "network": {"domains": [HOST]}}
    refused = await client.post(f"{ORG}/agents", json={"slug": "gw-agent", "display_name": "G", "spec": spec})
    assert refused.status_code == 422, refused.text
    assert "'spec', 'network', 'domains'" in str(refused.json()), refused.text


async def test_moving_or_removing_the_gateway_revokes_its_openings(
    client: AsyncClient, admin: str, enforce: None
) -> None:
    await _set_gateway(client, allow_egress=True)
    (first,) = [r for r in await rules_in_db("allowed") if r.host == HOST]
    assert first.origin == "mcp-gateway"
    await _set_gateway(client, "https://gw2.example.test/{server}/mcp", allow_egress=True)
    allowed = {r.host for r in await rules_in_db("allowed")}
    assert "gw2.example.test" in allowed and HOST not in allowed
    assert [r.host for r in await rules_in_db("revoked")] == [HOST]
    (audit,) = await _audit("egress.rule.revoke")
    assert audit["host"] == HOST and audit["origin"] == "mcp-gateway"
    gone = await client.delete(URL)
    assert gone.status_code == 204, gone.text
    assert {r.host for r in await rules_in_db("allowed")} == set()
    assert sorted(r.host for r in await rules_in_db("revoked")) == [HOST, "gw2.example.test"]


async def test_a_rule_the_gateway_did_not_create_is_kept(client: AsyncClient, admin: str) -> None:
    await _seed_rule(HOST, ["api"])
    await _set_gateway(client)
    assert (await client.delete(URL)).status_code == 204
    assert [r.host for r in await rules_in_db("allowed")] == [HOST]


async def test_without_a_gateway_nothing_changes(client: AsyncClient, admin: str) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.egress import org_runner_hosts

    created = await client.post(f"{ORG}/egress/rules", json=_rule(HOST, ["runners"]))
    assert created.status_code == 201, created.text
    organisation = await _organisation()
    async with session_scope() as session:
        assert HOST in await org_runner_hosts(session, organisation.id)
