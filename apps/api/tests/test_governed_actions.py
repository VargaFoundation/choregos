"""Governed actions in the core (ADR 0035, S20-01): proposed, decided, never run in the request that
approves them. A sufficient approval starts `action-<id>` in Temporal.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

EFFECTS = [
    {"effect": "connector.call", "with": {"connector": "entra-acme", "operation": "create_user",
                                          "arguments": {"upn": "{{ params.upn }}", "display_name": "Lea"}}},
]  # fmt: skip


@pytest.fixture(autouse=True)
async def directory(client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The directory these actions call, declared in the organisation: an action that calls an
    unknown operation, or one forbidden to this project, is refuseded as soon as it is proposed (S20-05)."""
    import choregos_adapters
    from choregos_adapters.fakes.entra import FakeEntra

    monkeypatch.setattr(choregos_adapters, "FAKE_ENTRA", FakeEntra())
    monkeypatch.setenv("CONNECTOR_ENTRA_CLIENT_SECRET", "connector-secret")
    body = {"name": "entra-acme", "type": "entra", "config": {"tenant_id": "acme", "client_id": "choregos"},
             "secret_refs": {"client_secret": "env:CONNECTOR_ENTRA_CLIENT_SECRET"}}  # fmt: skip
    created = await client.post("/api/v1/orgs/varga/connectors", json=body)
    assert created.status_code == 201, created.text


async def _member(client: AsyncClient, email: str, role: str) -> None:
    response = await client.post("/api/v1/orgs/varga/members", json={"email": email, "role": role})
    assert response.status_code in {200, 201}, response.text


async def _propose(client: AsyncClient, pid: str, **extra: Any) -> dict[str, Any]:
    body = {
        "kind": "entra.create_user",
        "title": "Create Lea's account",
        "params": {"upn": "lea@acme.test"},
        "effects": EFFECTS,
        **extra,
    }
    response = await client.post(f"/api/v1/projects/{pid}/actions", json=body)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _open_session(app: Any, email: str) -> AsyncClient:
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(other, email)
    return other


async def test_an_approved_action_goes_to_temporal_with_no_effect_in_the_request(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    pid = project["id"]
    await _member(client, "dev@varga.dev", "developer")
    dev = await _open_session(app, "dev@varga.dev")
    try:
        action = await _propose(dev, pid)
    finally:
        await dev.aclose()
    assert action["status"] == "pending_approval"
    assert action["proposed_by"] == {"kind": "user", "id": "dev@varga.dev", "via": "api"}

    decided = await client.post(
        f"/api/v1/projects/{pid}/actions/{action['id']}/decision", json={"decision": "approve"}
    )
    assert decided.status_code == 200, decided.text
    body = decided.json()
    assert body["status"] == "approved"
    assert body["temporal_wf_id"] == f"action-{action['id']}"
    assert body["journal"] == [], "no effect in the request: the response leaves first"
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    assert fake.started[f"action-{action['id']}"] == {"action_id": action["id"]}
    assert body["decisions"][0]["by"] == "admin@varga.dev"
    assert body["decisions"][0]["auth_age_seconds"] is not None


async def test_whoever_proposes_does_not_decide(client: AsyncClient, project: dict[str, Any]) -> None:
    action = await _propose(client, project["id"])
    refused = await client.post(
        f"/api/v1/projects/{project['id']}/actions/{action['id']}/decision", json={"decision": "approve"}
    )
    assert refused.status_code == 422 and "separation of duties" in refused.text


async def test_a_rejection_says_why_and_a_decided_action_is_not_decided_again(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    pid = project["id"]
    await _member(client, "dev@varga.dev", "developer")
    dev = await _open_session(app, "dev@varga.dev")
    try:
        action = await _propose(dev, pid)
    finally:
        await dev.aclose()
    base = f"/api/v1/projects/{pid}/actions/{action['id']}/decision"
    assert (await client.post(base, json={"decision": "reject"})).status_code == 422
    rejected = await client.post(base, json={"decision": "reject", "reason": "not hired yet"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert (await client.post(base, json={"decision": "approve"})).status_code == 409


async def test_a_developer_does_not_decide_and_neither_does_an_api_token(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    pid = project["id"]
    action = await _propose(client, pid)
    await _member(client, "dev@varga.dev", "developer")
    token = (await client.post("/api/v1/me/tokens", json={"name": "ci", "scopes": ["*"]})).json()["token"]
    dev = await _open_session(app, "dev@varga.dev")
    try:
        base = f"/api/v1/projects/{pid}/actions/{action['id']}/decision"
        assert (await dev.post(base, json={"decision": "approve"})).status_code == 403
    finally:
        await dev.aclose()
    by_token = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        refused = await by_token.post(
            base, json={"decision": "approve"}, headers={"Authorization": f"Bearer {token}"}
        )
        assert refused.status_code == 403 and "human session" in refused.text
    finally:
        await by_token.aclose()


async def test_a_session_that_is_too_old_must_authenticate_again(
    client: AsyncClient, project: dict[str, Any], app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from choregos_api import freshness

    pid = project["id"]
    await _member(client, "dev@varga.dev", "developer")
    dev = await _open_session(app, "dev@varga.dev")
    try:
        action = await _propose(dev, pid)
    finally:
        await dev.aclose()
    later = time.time() + 11 * 60
    monkeypatch.setattr(freshness.time, "time", lambda: later)
    refused = await client.post(
        f"/api/v1/projects/{pid}/actions/{action['id']}/decision", json={"decision": "approve"}
    )
    assert refused.status_code == 401
    assert refused.json()["errors"][0]["error"] == "step_up_required"
    assert (await client.get(f"/api/v1/projects/{pid}/actions/{action['id']}")).json()[
        "status"
    ] == "pending_approval"


async def test_two_are_needed_when_the_rule_asks_for_two(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    pid = project["id"]
    await _member(client, "po@varga.dev", "project_owner")
    await _member(client, "dev@varga.dev", "developer")
    dev = await _open_session(app, "dev@varga.dev")
    try:
        action = await _propose(dev, pid, approval={"approvers": [{"role": "project_owner", "min": 2}]})
    finally:
        await dev.aclose()
    base = f"/api/v1/projects/{pid}/actions/{action['id']}/decision"
    first = await client.post(base, json={"decision": "approve"})
    assert first.status_code == 200 and first.json()["status"] == "pending_approval"
    assert f"action-{action['id']}" not in get_temporal().started  # type: ignore[attr-defined]
    assert (await client.post(base, json={"decision": "approve"})).status_code == 409, (
        "not twice the same one"
    )
    po = await _open_session(app, "po@varga.dev")
    try:
        second = await po.post(base, json={"decision": "approve"})
    finally:
        await po.aclose()
    assert second.status_code == 200 and second.json()["status"] == "approved"
    assert f"action-{action['id']}" in get_temporal().started  # type: ignore[attr-defined]


async def test_of_two_simultaneous_approvals_only_one_starts_the_action(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    pid = project["id"]
    await _member(client, "po@varga.dev", "project_owner")
    await _member(client, "dev@varga.dev", "developer")
    dev = await _open_session(app, "dev@varga.dev")
    try:
        action = await _propose(dev, pid)
    finally:
        await dev.aclose()
    po = await _open_session(app, "po@varga.dev")
    try:
        base = f"/api/v1/projects/{pid}/actions/{action['id']}/decision"
        responses = await asyncio.gather(
            client.post(base, json={"decision": "approve"}), po.post(base, json={"decision": "approve"})
        )
    finally:
        await po.aclose()
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert list(get_temporal().started).count(f"action-{action['id']}") == 1  # type: ignore[attr-defined]


async def test_an_unknown_effect_is_refused_at_proposal(client: AsyncClient, project: dict[str, Any]) -> None:
    refused = await client.post(
        f"/api/v1/projects/{project['id']}/actions",
        json={"kind": "x", "title": "x", "effects": [{"effect": "erase.everything"}]},
    )
    assert refused.status_code == 422 and "erase.everything" in refused.text


async def test_evidence_is_handed_to_the_action_that_waits_for_it() -> None:
    """The evidence seam (S20-08): what establishes it hands it to `action-<id>`, by signal."""
    from choregos_api.services.actions import signal_evidence
    from choregos_api.temporal import FakeTemporal, set_temporal

    fake = FakeTemporal()
    set_temporal(fake)
    try:
        await signal_evidence("a1", 2, False, "the key is still there")
    finally:
        set_temporal(None)
    expected = {"position": 2, "ok": False, "detail": "the key is still there"}
    assert fake.signals == [("action-a1", "evidence", expected)]


async def test_a_rule_without_step_up_does_not_demand_a_recent_authentication(
    client: AsyncClient, project: dict[str, Any], app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An extension's policy may not demand a recent authentication (S20-08): `step_up_minutes`
    absent. An action proposed through the API always has one; this one is decided without a 401."""
    import time

    from choregos_api import freshness
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    pid = project["id"]
    await _member(client, "dev@varga.dev", "developer")
    dev = await _open_session(app, "dev@varga.dev")
    try:
        action = await _propose(dev, pid)
    finally:
        await dev.aclose()
    async with session_scope(orgs="*") as session:
        row = await session.get(Action, action["id"])
        assert row is not None
        row.approval = {**row.approval, "step_up_minutes": None}
    later = time.time() + 11 * 60
    monkeypatch.setattr(freshness.time, "time", lambda: later)
    decided = await client.post(
        f"/api/v1/projects/{pid}/actions/{action['id']}/decision", json={"decision": "approve"}
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["decisions"][0]["auth_age_seconds"] >= 11 * 60, "the age is still recorded"
