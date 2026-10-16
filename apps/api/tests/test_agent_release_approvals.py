"""Approvals before an agent revision reaches production (S40-02b, ADR 0071).

A promotion under a policy with `approvals: N` is stored as `pending_approval`; production moves when N
different people, each in a fresh session, have approved — and neither the requester nor the revision's
author when `separation_of_duties`. Each guard removed once turns a case below red.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import AsyncClient

from .agent_scopes_kit import ADMIN, ORG, add_member, as_user, create_agent, make_project
from .test_agent_release_channels import agent_of, new_agent, only_project_roles, release, save, token_client

pytestmark = pytest.mark.usefixtures("admin")

APPROVAL = {"on_publish": "candidate", "stable": {"approvals": 1}}
CAROL, DAVE, ERIN = "carol@varga.dev", "dave@varga.dev", "erin@varga.dev"


async def org_admins(client: AsyncClient, *emails: str) -> None:
    for email in emails:
        await add_member(client, email, "org_admin")


async def decide(client: AsyncClient, release_id: str, decision: str, reason: str | None = None) -> Any:
    return await client.post(
        f"{ORG}/agents/reviewer/releases/{release_id}/decision", json={"decision": decision, "reason": reason}
    )


async def request_v2(client: AsyncClient, policy: dict[str, Any] = APPROVAL) -> dict[str, Any]:
    """An agent in production at v1 with a candidate v2, and a promotion of v2 waiting for approval."""
    await new_agent(client, policy=policy)
    await save(client, "reviewer", 2)
    asked = await release(client, "reviewer", channel="stable", version=2, reason="ship it")
    assert asked.status_code == 201, asked.text
    return dict(asked.json())


async def test_a_promotion_waits_for_an_approval_then_production_moves(client: AsyncClient) -> None:
    await org_admins(client, DAVE)
    await as_user(client, ADMIN)
    waiting = await request_v2(client)
    assert (waiting["status"], waiting["approvals_required"], waiting["decisions"]) == (
        "pending_approval",
        1,
        [],
    )
    assert waiting["done_at"] is None and waiting["previous_version"] == 1
    agent = await agent_of(client)
    assert agent["stable_version"] == 1, "production does not move on request"
    assert agent["pending_release"]["id"] == waiting["id"] and agent["candidate_version"] == 2
    readiness = (await client.get(f"{ORG}/agents/reviewer/versions/2/readiness")).json()
    note = next(c for c in readiness["checks"] if c["name"] == "approvals")
    assert note["required"] is False and "1 approval" in note["detail"]

    await as_user(client, DAVE)
    done = await decide(client, waiting["id"], "approve")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["status"] == "done" and body["done_at"] is not None
    assert body["decisions"][0]["by"] == DAVE and body["decisions"][0]["decision"] == "approve"
    assert isinstance(body["decisions"][0]["auth_age_seconds"], int), "the age of the sign-in is recorded"
    agent = await agent_of(client)
    assert (agent["stable_version"], agent["candidate_version"], agent["pending_release"]) == (2, None, None)


async def test_separation_of_duties_keeps_the_requester_and_the_author_out(client: AsyncClient) -> None:
    await org_admins(client, CAROL, DAVE)
    await as_user(client, ADMIN)
    await new_agent(client, policy=APPROVAL)
    await as_user(client, CAROL)
    await save(client, "reviewer", 2)  # Carol writes the revision
    await as_user(client, ADMIN)
    waiting = (await release(client, "reviewer", channel="stable", version=2)).json()  # the admin asks
    me = await decide(client, waiting["id"], "approve")
    assert me.status_code == 422 and "separation of duties" in me.text, "the requester"
    await as_user(client, CAROL)
    author = await decide(client, waiting["id"], "approve")
    assert author.status_code == 422 and "separation of duties" in author.text, "the author"
    assert (await agent_of(client))["stable_version"] == 1
    await as_user(client, DAVE)
    assert (await decide(client, waiting["id"], "approve")).status_code == 200
    assert (await agent_of(client))["stable_version"] == 2


async def test_separation_of_duties_can_be_switched_off(client: AsyncClient) -> None:
    await as_user(client, ADMIN)
    policy = {"on_publish": "candidate", "stable": {"approvals": 1, "separation_of_duties": False}}
    waiting = await request_v2(client, policy)
    assert (await decide(client, waiting["id"], "approve")).status_code == 200


async def test_an_approval_needs_a_fresh_sign_in_and_a_person(
    client: AsyncClient, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import fraicheur

    await org_admins(client, DAVE)
    await as_user(client, ADMIN)
    waiting = await request_v2(client)
    async with await token_client(app, client) as machine:
        refused = await machine.post(
            f"{ORG}/agents/reviewer/releases/{waiting['id']}/decision", json={"decision": "approve"}
        )
        assert refused.status_code == 403 and "human session" in refused.text
    await as_user(client, DAVE)
    later = time.time() + 11 * 60
    monkeypatch.setattr(fraicheur.time, "time", lambda: later)
    old = await decide(client, waiting["id"], "approve")
    assert old.status_code == 401 and old.json()["errors"][0]["error"] == "step_up_required"
    monkeypatch.undo()
    assert (await agent_of(client))["pending_release"]["decisions"] == [], "nothing was recorded"
    assert (await decide(client, waiting["id"], "approve")).status_code == 200


async def test_one_request_at_a_time(client: AsyncClient) -> None:
    await as_user(client, ADMIN)
    await request_v2(client)
    await save(client, "reviewer", 3)
    second = await release(client, "reviewer", channel="stable", version=3)
    assert second.status_code == 409 and "already waits" in second.text


async def test_production_moved_meanwhile_supersedes_the_request(client: AsyncClient) -> None:
    await org_admins(client, DAVE)
    await as_user(client, ADMIN)
    await new_agent(client)
    await save(client, "reviewer", 2)  # production v2 under the default policy
    policy = {"on_publish": "candidate", "stable": {"approvals": 1}}
    assert (await client.put(f"{ORG}/agents/reviewer/release-policy", json=policy)).status_code == 200
    await save(client, "reviewer", 3)
    waiting = (await release(client, "reviewer", channel="stable", version=3)).json()
    assert waiting["previous_version"] == 2
    back = await release(client, "reviewer", channel="stable", version=1, reason="incident")
    assert back.status_code == 201 and back.json()["kind"] == "rollback" and back.json()["status"] == "done"
    await as_user(client, DAVE)
    stale = await decide(client, waiting["id"], "approve")
    assert stale.status_code == 409 and "superseded" in stale.text
    assert (await agent_of(client))["stable_version"] == 1, "the rollback stands"
    inbox = (await client.get(f"{ORG}/agent-releases", params={"status": "superseded"})).json()
    assert [r["id"] for r in inbox] == [waiting["id"]], "the status survives the 409"
    again = await decide(client, waiting["id"], "approve")
    assert again.status_code == 409 and "already superseded" in again.text


async def test_a_rejection_says_why_and_a_requester_may_cancel(client: AsyncClient) -> None:
    await org_admins(client, DAVE, ERIN)
    await as_user(client, ADMIN)
    waiting = await request_v2(client)
    await as_user(client, DAVE)
    assert (await decide(client, waiting["id"], "reject")).status_code == 422
    rejected = await decide(client, waiting["id"], "reject", "not before the audit")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert rejected.json()["decisions"][0]["reason"] == "not before the audit"
    assert (await agent_of(client))["stable_version"] == 1
    assert (await decide(client, waiting["id"], "approve")).status_code == 409, "a decided request is closed"

    await as_user(client, ADMIN)
    again = await release(client, "reviewer", channel="stable", version=2)
    assert again.status_code == 201 and again.json()["status"] == "pending_approval"
    cancelled = await decide(client, again.json()["id"], "cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    third = await release(client, "reviewer", channel="stable", version=2)
    await as_user(client, ERIN)
    by_admin = await decide(client, third.json()["id"], "cancel")
    assert by_admin.status_code == 200, "an org_admin cancels any request"


async def test_two_approvals_need_two_different_people(client: AsyncClient) -> None:
    await org_admins(client, CAROL, DAVE)
    await as_user(client, ADMIN)
    waiting = await request_v2(client, {"on_publish": "candidate", "stable": {"approvals": 2}})
    await as_user(client, CAROL)
    first = await decide(client, waiting["id"], "approve")
    assert first.status_code == 200 and first.json()["status"] == "pending_approval"
    assert (await agent_of(client))["stable_version"] == 1
    twice = await decide(client, waiting["id"], "approve")
    assert twice.status_code == 409 and "already approved" in twice.text
    await as_user(client, DAVE)
    last = await decide(client, waiting["id"], "approve")
    assert last.status_code == 200 and last.json()["status"] == "done"
    assert [d["by"] for d in last.json()["decisions"]] == [CAROL, DAVE]
    assert (await agent_of(client))["stable_version"] == 2


async def test_a_rollback_never_waits_and_saving_with_on_publish_stable_waits(client: AsyncClient) -> None:
    await as_user(client, ADMIN)
    await new_agent(client)
    await save(client, "reviewer", 2)
    policy = {"on_publish": "stable", "stable": {"approvals": 1}}
    assert (await client.put(f"{ORG}/agents/reviewer/release-policy", json=policy)).status_code == 200
    saved = await save(client, "reviewer", 3)
    assert saved["channels"] == [], "saved, not in production"
    agent = await agent_of(client)
    assert agent["stable_version"] == 2 and agent["pending_release"]["version"] == 3
    back = await release(client, "reviewer", channel="stable", version=1)
    assert back.status_code == 201 and back.json()["status"] == "done" and back.json()["kind"] == "rollback"
    await save(client, "reviewer", 4)
    waiting = (await agent_of(client))["pending_release"]
    assert waiting["version"] == 4, "a newer revision replaces the request of an older one"
    listed = (await client.get(f"{ORG}/agent-releases", params={"status": "pending_approval"})).json()
    assert [r["version"] for r in listed] == [4]


async def test_the_inbox_lists_only_the_agents_the_caller_may_see(client: AsyncClient) -> None:
    await make_project(client, "dev")
    await make_project(client, "ops")
    await add_member(client, CAROL, "project_owner", project="ops")
    await add_member(client, "owner@varga.dev", "project_owner", project="dev")
    await as_user(client, ADMIN)
    policy = {"on_publish": "candidate", "stable": {"approvals": 1}}
    shared = await create_agent(client, "reviewer", spec={"instructions": "Review."}, release_policy=policy)
    assert shared.status_code == 201
    mine = await create_agent(
        client,
        "dev-helper",
        scope="project",
        project="dev",
        spec={"instructions": "Help."},
        release_policy=policy,
    )
    assert mine.status_code == 201, mine.text
    for slug in ("reviewer", "dev-helper"):
        await save(client, slug, 2)
        assert (await release(client, slug, channel="stable", version=2)).status_code == 201
    inbox = (await client.get(f"{ORG}/agent-releases", params={"status": "pending_approval"})).json()
    assert {r["agent"] for r in inbox} == {"reviewer", "dev-helper"}
    await as_user(client, CAROL)
    await only_project_roles()
    visible = (await client.get(f"{ORG}/agent-releases", params={"status": "pending_approval"})).json()
    assert [r["agent"] for r in visible] == ["reviewer"], "a member of ops does not see dev's agent"
    await as_user(client, "owner@varga.dev")
    await only_project_roles()
    owner = (await client.get(f"{ORG}/agent-releases", params={"status": "pending_approval"})).json()
    assert "dev-helper" in {r["agent"] for r in owner}
    hidden = await client.post(f"{ORG}/agents/dev-helper/releases/x/decision", json={"decision": "cancel"})
    assert hidden.status_code == 404


async def test_a_project_owner_decides_for_their_project_agent_but_not_for_an_organisation_agent(
    client: AsyncClient,
) -> None:
    await make_project(client, "dev")
    await add_member(client, "owner@varga.dev", "project_owner", project="dev")
    await as_user(client, ADMIN)
    policy = {"on_publish": "candidate", "stable": {"approvals": 1}}
    await new_agent(client, policy=policy)
    await save(client, "reviewer", 2)
    waiting = (await release(client, "reviewer", channel="stable", version=2)).json()
    await as_user(client, "owner@varga.dev")
    refused = await decide(client, waiting["id"], "approve")
    assert refused.status_code == 403, "approvers hold the release right on the agent"


async def test_retiring_an_agent_cancels_its_waiting_request(client: AsyncClient) -> None:
    await as_user(client, ADMIN)
    waiting = await request_v2(client)
    retired = await client.post(f"{ORG}/agents/reviewer/lifecycle", json={"to": "retired"})
    assert retired.status_code == 200, retired.text
    listed = (await client.get(f"{ORG}/agent-releases", params={"status": "cancelled"})).json()
    assert [r["id"] for r in listed] == [waiting["id"]]


async def test_the_approvals_gate_is_not_loosened_without_a_fresh_sign_in(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import fraicheur

    await as_user(client, ADMIN)
    await new_agent(client, policy={"on_publish": "candidate", "stable": {"approvals": 2}})
    later = time.time() + 11 * 60
    monkeypatch.setattr(fraicheur.time, "time", lambda: later)
    fewer = await client.put(
        f"{ORG}/agents/reviewer/release-policy", json={"on_publish": "candidate", "stable": {"approvals": 1}}
    )
    assert fewer.status_code == 401
    shared = await client.put(
        f"{ORG}/agents/reviewer/release-policy",
        json={"on_publish": "candidate", "stable": {"approvals": 2, "separation_of_duties": False}},
    )
    assert shared.status_code == 401
    more = await client.put(
        f"{ORG}/agents/reviewer/release-policy", json={"on_publish": "candidate", "stable": {"approvals": 3}}
    )
    assert more.status_code == 200


async def test_the_gesture_check_sees_each_approval(client: AsyncClient) -> None:
    from choregos_api import greffons

    seen: list[dict[str, Any]] = []

    def refuse_approvals(_session: Any, demand: Any) -> None:
        seen.append(demand.cible)
        if demand.cible.get("decision") == "approve":
            raise greffons.GesteRefuse("approvals are closed this week")

    await org_admins(client, DAVE)
    await as_user(client, ADMIN)
    greffons.declarer_un_controle_de_geste("agent.release", "freeze", refuse_approvals)
    try:
        waiting = await request_v2(client)
        await as_user(client, DAVE)
        refused = await decide(client, waiting["id"], "approve")
    finally:
        greffons.reinitialiser_les_controles()
    assert refused.status_code == 403 and "freeze: approvals are closed" in refused.text
    assert [c.get("decision") for c in seen] == [None, "approve"], "the request, then the approval"
    assert (await agent_of(client))["stable_version"] == 1
