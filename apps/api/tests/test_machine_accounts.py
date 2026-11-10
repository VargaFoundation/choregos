# SPDX-License-Identifier: Apache-2.0
"""Machine accounts (ADR 0131): an application calls the A2A door under its own name."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login

ENDPOINT = "/a2a/orgs/varga"
CARD = f"{ENDPOINT}/.well-known/agent-card.json"
OPT_IN = "a2a:\n  skill: ship-a-change\n  description: Ship a change through review and release.\n"


def _base(project: dict[str, Any]) -> str:
    return f"/api/v1/projects/{project['id']}/machine-accounts"


async def _publish(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_core.dsl import template_yaml

    tracker = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    text = template_yaml("default-simple").rstrip("\n") + "\n" + OPT_IN
    published = await client.put(f"/api/v1/projects/{project['id']}/workflow", json={"yaml": text})
    assert published.status_code == 200, published.text


async def _machine(
    client: AsyncClient, project: dict[str, Any], *, budget: int | None = None
) -> tuple[dict[str, Any], str]:
    body: dict[str, Any] = {"slug": "partner-app", "display_name": "Partner app"}
    if budget is not None:
        body["writes_per_day"] = budget
    created = await client.post(_base(project), json=body)
    assert created.status_code == 201, created.text
    token = await client.post(f"{_base(project)}/partner-app/tokens", json={"name": "first"})
    assert token.status_code == 201, token.text
    return created.json(), str(token.json()["token"])


def _message(message_id: str = "m-1") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": message_id,
                "parts": [{"kind": "text", "text": "Add credit notes"}],
            }
        },
    }


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_an_application_starts_a_workflow_under_its_own_name(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    await _publish(client, project)
    account, token = await _machine(client, project)
    client.cookies.clear()
    card = (await client.get(CARD, headers=_bearer(token))).json()
    assert [s["id"] for s in card["skills"]] == ["billing-api/ship-a-change"]
    answer = (await client.post(ENDPOINT, json=_message(), headers=_bearer(token))).json()
    assert answer["result"]["status"]["state"] == "working", answer
    async with session_scope() as session:
        item = await session.get(WorkItem, answer["result"]["id"])
        assert item is not None
        assert item.initiated_by == f"machine:{account['address']}"


async def test_the_account_is_listed_with_its_tokens_and_never_their_value(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    _, token = await _machine(client, project)
    listed = await client.get(_base(project))
    assert listed.status_code == 200, listed.text
    assert token not in listed.text
    entry = listed.json()[0]
    assert entry["address"].endswith("@machines.choregos.invalid")
    assert entry["tokens"][0]["scopes"] == ["a2a"]
    assert entry["tokens"][0]["project"] == "varga:billing-api"
    assert entry["tokens"][0]["expires_at"] is not None


async def test_it_outlives_the_person_who_created_it(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import Membership, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete, select

    await _publish(client, project)
    _, token = await _machine(client, project)
    client.cookies.clear()
    async with session_scope(orgs="*") as session:
        creator = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        await session.execute(delete(Membership).where(Membership.user_id == creator.id))
    assert (await client.get(CARD, headers=_bearer(token))).status_code == 200


async def test_its_token_opens_neither_the_rest_api_nor_the_mcp_door(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    _, token = await _machine(client, project)
    client.cookies.clear()
    rest = await client.get(f"/api/v1/projects/{project['id']}", headers=_bearer(token))
    assert rest.status_code == 403, rest.text
    ping = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    assert (await client.post("/mcp", json=ping, headers=_bearer(token))).status_code == 403


async def test_it_cannot_answer_a_task(client: AsyncClient, project: dict[str, Any]) -> None:
    await _publish(client, project)
    _, token = await _machine(client, project)
    client.cookies.clear()
    task = (await client.post(ENDPOINT, json=_message(), headers=_bearer(token))).json()["result"]
    reply = _message("m-2")
    reply["params"]["message"]["taskId"] = task["id"]
    refused = (await client.post(ENDPOINT, json=reply, headers=_bearer(token))).json()
    assert refused["error"]["code"] == -32004, refused


async def test_its_principal_is_never_a_person(client: AsyncClient) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from choregos_api.deps import _principal_from_user
    from choregos_api.services.initiator import of_principal

    async with session_scope(orgs="*") as session:
        user = User(email="x.p@machines.choregos.invalid", display_name="x", is_service=True)
        session.add(user)
        await session.flush()
        principal = await _principal_from_user(session, user)
    assert principal.kind == "machine"
    assert of_principal(principal, "a2a") == "machine:x.p@machines.choregos.invalid"


async def test_it_cannot_come_in_through_oauth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a provider that vouches for the address gets nothing: the OAuth path is a person's."""
    from choregos_api.config import get_settings
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from choregos_api.mcp import caller

    address = "y.p@machines.choregos.invalid"

    async def vouches(*_: Any) -> dict[str, Any]:
        return {"sub": "someone", "email": address, "email_verified": True, "scope": "a2a"}

    monkeypatch.setattr(caller, "validate", vouches)
    async with session_scope(orgs="*") as session:
        session.add(User(email=address, display_name="y", is_service=True))
        await session.flush()
        with pytest.raises(caller.Refusal) as refused:
            await caller._by_oauth(session, "raw", get_settings(), scopes=caller.SCOPES_A2A)
    assert refused.value.status == 403


async def test_a_run_of_a_machine_acts_for_nobody() -> None:
    from choregos_api.knowledge.on_behalf import PERSON_KINDS

    assert "machine" not in PERSON_KINDS


async def test_its_budget_is_its_own(client: AsyncClient, project: dict[str, Any]) -> None:
    await _publish(client, project)
    _, token = await _machine(client, project, budget=1)
    client.cookies.clear()
    first = (await client.post(ENDPOINT, json=_message("m-1"), headers=_bearer(token))).json()
    assert "result" in first, first
    second = (await client.post(ENDPOINT, json=_message("m-2"), headers=_bearer(token))).json()
    assert second["error"]["code"] == -32050 and "1 writes" in second["error"]["message"], second


@pytest.mark.parametrize("days", [0, 366])
async def test_a_token_always_expires_within_a_year(
    client: AsyncClient, project: dict[str, Any], days: int
) -> None:
    created = await client.post(_base(project), json={"slug": "partner-app"})
    assert created.status_code == 201, created.text
    refused = await client.post(
        f"{_base(project)}/partner-app/tokens", json={"name": "t", "expires_in_days": days}
    )
    assert refused.status_code == 422, refused.text


async def test_revoking_a_token_leaves_the_account(client: AsyncClient, project: dict[str, Any]) -> None:
    await _publish(client, project)
    _, first = await _machine(client, project)
    second = (await client.post(f"{_base(project)}/partner-app/tokens", json={"name": "next"})).json()
    entry = (await client.get(_base(project))).json()[0]
    old = next(t["id"] for t in entry["tokens"] if t["name"] == "first")
    gone = await client.delete(f"{_base(project)}/partner-app/tokens/{old}")
    assert gone.status_code == 204, gone.text
    client.cookies.clear()
    assert (await client.get(CARD, headers=_bearer(first))).status_code == 401
    assert (await client.get(CARD, headers=_bearer(second["token"]))).status_code == 200


async def test_removing_the_account_ends_it_and_keeps_its_slug(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _publish(client, project)
    _, token = await _machine(client, project)
    assert (await client.delete(f"{_base(project)}/partner-app")).status_code == 204
    assert (await client.get(_base(project))).json() == []
    again = await client.post(_base(project), json={"slug": "partner-app"})
    assert again.status_code == 409, again.text
    client.cookies.clear()
    assert (await client.get(CARD, headers=_bearer(token))).status_code == 401


async def test_an_ended_account_opens_nothing_even_with_a_token_left(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Removal deletes the tokens; the door does not rely on that having happened."""
    from choregos_api.db.models import MachineAccount
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from sqlalchemy import update

    await _publish(client, project)
    _, token = await _machine(client, project)
    client.cookies.clear()
    async with session_scope(orgs="*") as session:
        await session.execute(update(MachineAccount).values(removed_at=utcnow()))
    assert (await client.get(CARD, headers=_bearer(token))).status_code == 401


async def test_a_member_who_cannot_manage_members_cannot_create_one(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    added = await client.post(
        "/api/v1/orgs/varga/members",
        json={"email": "dev@varga.dev", "role": "developer", "project_slug": "billing-api"},
    )
    assert added.status_code in (200, 201), added.text
    client.cookies.clear()
    await login(client, "dev@varga.dev")
    refused = await client.post(_base(project), json={"slug": "partner-app"})
    assert refused.status_code == 403, refused.text


async def test_it_is_not_in_the_members_list(client: AsyncClient, project: dict[str, Any]) -> None:
    account, _ = await _machine(client, project)
    members = await client.get("/api/v1/orgs/varga/members")
    assert members.status_code == 200, members.text
    assert account["address"] not in members.text
