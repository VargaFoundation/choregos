"""Approval by a named group (#401, ADR 0052 §7): the organisation names the identity-provider group
whose members approve what an operation proposes — `netops-approvers` for a firewall rule. A rank is
still needed; the group is a second condition. A project owner outside the group is refused.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"
GROUP = "netops-approvers"


@pytest.fixture
def firewall(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.netops import FakePfSense

    fake = FakePfSense()
    monkeypatch.setattr(choregos_adapters, "FAKE_NETOPS", {"pfsense": fake})
    monkeypatch.setenv("APPROVAL_TEST_PFSENSE_KEY", "-".join(("pfsense", "approval", "test")))
    yield fake


async def _member(client: AsyncClient, email: str, role: str, groups: list[str]) -> None:
    """An organisation member whose identity provider says `groups` (as its sign-in would store them)."""
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    added = await client.post(f"{ORG}/members", json={"email": email, "role": role})
    assert added.status_code in {200, 201}, added.text
    async with session_scope() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.groups = groups


async def _session(app: Any, email: str) -> AsyncClient:
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(other, email)
    return other


async def _proposed_rule_change(client: AsyncClient, project: dict[str, Any]) -> str:
    """The pfSense connector, its update approved by `netops-approvers`, and a netops run proposing one."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    body = {"name": "pfsense", "type": "pfsense", "config": {"base_url": "https://fw.example.test"},
            "secret_refs": {"api_key": "env:APPROVAL_TEST_PFSENSE_KEY"}}  # fmt: skip
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    named = await client.patch(
        f"{ORG}/connectors/pfsense/operations/update_firewall_rule", json={"approver_group": GROUP}
    )
    assert named.status_code == 200 and named.json()["approver_group"] == GROUP, named.text
    spec = {"mcp_servers": [{"connector": "pfsense", "tools": ["update_firewall_rule"]}]}
    agent = await client.post(
        f"{ORG}/agents", json={"slug": "netops", "display_name": "Netops", "spec": spec}
    )
    assert agent.status_code == 201, agent.text
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/net#2", title="open 9443", state="ready")
        session.add(item)
        await session.flush()
        run = Run(id="run-group-1", work_item_id=item.id, project_id=project["id"], stage_role="x",
                  transition_id="t", status="running", agent_slug="netops", agent_version=1)  # fmt: skip
        session.add(run)
    token = mint_run_token(
        "run-group-1", project_slug="billing-api", work_item_key="varga/net#2", ttl_minutes=30
    )
    called = await client.post(
        "/api/v1/internal/runs/run-group-1/tools/pfsense__update_firewall_rule",
        headers={"Authorization": f"Bearer {token}"},
        json={"tracker": "1700000102", "changes": {"destination_port": "9443"}},
    )
    assert called.json()["status_code"] == 202, called.text
    return str(called.json()["result"]["action"])


async def test_only_a_member_of_the_named_group_approves_a_firewall_rule(
    client: AsyncClient, project: dict[str, Any], firewall: Any, app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    action_id = await _proposed_rule_change(client, project)
    path = f"/api/v1/projects/{project['id']}/actions/{action_id}"
    action = (await client.get(path)).json()
    assert action["approval"]["approvers"] == [{"role": "developer", "group": GROUP, "min": 1}]

    await _member(client, "owner@varga.dev", "project_owner", ["platform-team"])
    await _member(client, "dev@varga.dev", "developer", [])
    await _member(client, "net@varga.dev", "developer", [GROUP, "platform-team"])
    for outsider in ("owner@varga.dev", "dev@varga.dev"):
        session = await _session(app, outsider)
        try:
            refused = await session.post(f"{path}/decision", json={"decision": "approve"})
        finally:
            await session.aclose()
        assert refused.status_code == 403, (outsider, refused.text)
        assert GROUP in refused.text
    assert (await client.get(path)).json()["status"] == "pending_approval"

    member = await _session(app, "net@varga.dev")
    try:
        approved = await member.post(f"{path}/decision", json={"decision": "approve"})
    finally:
        await member.aclose()
    assert approved.status_code == 200 and approved.json()["status"] == "approved", approved.text
    assert f"action-{action_id}" in get_temporal().started
    assert firewall.writes == 0, "approved in the request, played by the ActionWorkflow"


async def test_a_read_has_no_approver_and_the_group_can_be_cleared(
    client: AsyncClient, admin: str, firewall: Any
) -> None:
    body = {"name": "pfsense", "type": "pfsense", "config": {"base_url": "https://fw.example.test"},
            "secret_refs": {"api_key": "env:APPROVAL_TEST_PFSENSE_KEY"}}  # fmt: skip
    assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    read = await client.patch(
        f"{ORG}/connectors/pfsense/operations/list_aliases", json={"approver_group": GROUP}
    )
    assert read.status_code == 422 and "nobody approves it" in read.text
    operation = f"{ORG}/connectors/pfsense/operations/create_firewall_rule"
    assert (await client.patch(operation, json={"approver_group": GROUP})).json()["approver_group"] == GROUP
    assert (await client.patch(operation, json={"policy": "approval"})).json()["approver_group"] == GROUP
    assert (await client.patch(operation, json={"approver_group": None})).json()["approver_group"] is None


async def test_the_identity_provider_s_groups_are_kept_at_sign_in_and_replaced_at_the_next(
    client: AsyncClient, org: str
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from choregos_api.routers.auth import terminer_la_connexion
    from sqlalchemy import select

    async def sign_in(groups: list[str]) -> list[str]:
        async with session_scope() as session:
            await terminer_la_connexion(
                session, email="net@varga.dev", nom="Net", sujet="idp|net", groupes=groups, cible="/",
                canal="test", settings=get_settings(),
            )  # fmt: skip
        async with session_scope() as session:
            user = (await session.execute(select(User).where(User.email == "net@varga.dev"))).scalar_one()
            return list(user.groups)

    assert await sign_in([GROUP, "choregos:varga:developers"]) == ["choregos:varga:developers", GROUP]
    assert await sign_in(["choregos:varga:developers"]) == ["choregos:varga:developers"], (
        "a group withdrawn at the IdP is withdrawn at the next sign-in"
    )
