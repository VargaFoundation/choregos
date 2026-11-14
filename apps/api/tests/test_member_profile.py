"""A member's profile page (S66-42, ADR 0162): one read, shaped by who reads it.

An administrator reads everything; a member reads the basic card of another member; the person reads their
own full profile without the administration section; a caller outside the organisation is refused before any
lookup, and a person who is not a member is not found. The work section lists the human requests waiting on
the person, addressed to them or to a group they are in, their decisions and the runs they started.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from choregos_core import utcnow
from httpx import AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"


@pytest.fixture
def elsewhere(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The development sign-in lands a new person in every organisation unless a default one is set: from
    here on it lands them in `other`, so a varga role is only one a test grants."""
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", "other")
    reset_settings_cache()
    yield
    reset_settings_cache()


async def _member(client: AsyncClient, email: str, role: str = "developer") -> str:
    added = await client.post(f"{ORG}/members", json={"email": email, "role": role})
    assert added.status_code == 201, added.text
    return str(added.json()["user_id"])


async def _profile(client: AsyncClient, user_id: str) -> Any:
    return await client.get(f"{ORG}/members/{user_id}/profile")


async def _other_organisation() -> None:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(Organization(slug="other", name="Other"))


async def _work(project_id: str, dev_email: str) -> dict[str, str]:
    """A work item with three waiting requests (to the person, to a group they are in, to someone else), one
    decided by them, and a run of a work item they started."""
    from choregos_api.db.models import HumanRequest, Run, WorkItem
    from choregos_api.db.session import session_scope

    now = utcnow()
    async with session_scope() as session:
        item = WorkItem(project_id=project_id, tracker_key="BILL-1", title="Refund flow")
        started = WorkItem(
            project_id=project_id,
            tracker_key="BILL-2",
            title="Invoices",
            initiated_by=f"person:{dev_email}",
        )
        session.add_all([item, started])
        await session.flush()
        to_person = HumanRequest(
            work_item_id=item.id,
            project_id=project_id,
            kind="approval",
            requested_at=now - timedelta(hours=1),
            assignee={"user": dev_email.upper()},
        )
        to_group = HumanRequest(
            work_item_id=item.id,
            project_id=project_id,
            kind="question",
            requested_at=now - timedelta(hours=2),
            assignee={"group": "reviewers"},
        )
        to_someone_else = HumanRequest(
            work_item_id=item.id,
            project_id=project_id,
            kind="approval",
            requested_at=now,
            assignee={"user": "someone@varga.dev", "group": "reviewers"},
        )
        decided = HumanRequest(
            work_item_id=item.id,
            project_id=project_id,
            kind="approval",
            requested_at=now - timedelta(days=1),
            decided_at=now - timedelta(hours=20),
            decided_by=dev_email,
            decision={"approved": True},
        )
        session.add_all([to_person, to_group, to_someone_else, decided])
        session.add(
            Run(
                id="BILL-2-build-1",
                work_item_id=started.id,
                project_id=project_id,
                stage_role="build",
                status="succeeded",
                initiated_by=f"person:{dev_email}",
                cost_usd=0.42,
            )
        )
        await session.flush()
        return {
            "person": to_person.id,
            "group": to_group.id,
            "else": to_someone_else.id,
            "decided": decided.id,
        }


async def _book_spend(project_id: str, user_id: str, cost: float) -> None:
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(CostLedger(project_id=project_id, user_id=user_id, cost_usd=cost, ts=utcnow()))
        session.add(
            CostLedger(
                project_id=project_id, user_id=user_id, cost_usd=99.0, ts=utcnow() - timedelta(days=62)
            )
        )


async def test_an_administrator_reads_the_whole_profile(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    dev = await _member(client, "dev@varga.dev")
    group = await client.post(f"{ORG}/groups", json={"slug": "reviewers", "name": "Reviewers"})
    assert group.status_code == 201, group.text
    joined = await client.post(f"{ORG}/groups/reviewers/members", json={"user_id": dev})
    assert joined.status_code in (200, 201), joined.text
    requests = await _work(project["id"], "dev@varga.dev")
    await _book_spend(project["id"], dev, 1.25)

    response = await _profile(client, dev)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["view"] == "full" and body["status"] == "active"
    assert body["email"] == "dev@varga.dev" and body["org_role"] == "developer"
    assert body["groups"] == [{"slug": "reviewers", "name": "Reviewers", "source": "internal"}]
    assert body["origin"] == "manual", "invited by an administrator, never signed in"
    assert body["sessions"] == {"open": 0, "last_activity_at": None}
    pending = {r["id"]: r for r in body["work"]["pending_requests"]}
    assert set(pending) == {requests["person"], requests["group"]}, "not the one addressed to someone else"
    assert pending[requests["person"]]["via"] == "person"
    assert pending[requests["group"]]["via"] == "group" and pending[requests["group"]]["group"] == "reviewers"
    assert pending[requests["person"]]["work_item_key"] == "BILL-1"
    [decision] = body["work"]["decisions"]
    assert decision["request_id"] == requests["decided"] and decision["approved"] is True
    [run] = body["work"]["runs"]
    assert run["work_item_key"] == "BILL-2" and run["cost_usd"] == pytest.approx(0.42)
    assert body["work"]["spend_month_usd"] == pytest.approx(1.25), "this month's lines that name them only"
    administration = body["administration"]
    assert administration["api_tokens"] == 0
    actions = {(e["action"], e["target_id"]) for e in administration["audit"]}
    assert ("member.add", dev) in actions


async def test_a_member_reads_the_basic_card_of_another_member(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    dev = await _member(client, "dev@varga.dev")
    await _member(client, "viewer@varga.dev", role="viewer")
    await _work(project["id"], "dev@varga.dev")

    await login(client, "viewer@varga.dev")
    response = await _profile(client, dev)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["view"] == "basic"
    assert body["email"] == "dev@varga.dev" and body["org_role"] == "developer"
    for hidden in ("origin", "last_login_at", "sessions", "work", "administration"):
        assert body[hidden] is None, hidden


async def test_the_person_reads_their_own_work_but_not_the_administration(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    dev = await _member(client, "dev@varga.dev")
    requests = await _work(project["id"], "dev@varga.dev")

    await login(client, "dev@varga.dev")
    body = (await _profile(client, dev)).json()

    assert body["view"] == "full" and body["administration"] is None
    assert body["sessions"]["open"] == 1 and body["last_login_at"]
    assert [r["id"] for r in body["work"]["pending_requests"]] == [requests["person"]], (
        "not in the group: only the request addressed to them"
    )


async def test_outside_the_organisation_the_profile_is_refused_and_a_stranger_is_not_found(
    client: AsyncClient, admin: str, elsewhere: None
) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope

    dev = await _member(client, "dev@varga.dev")
    async with session_scope() as session:
        stranger = User(email="nobody@varga.dev", display_name="Nobody")
        session.add(stranger)
        await session.flush()
        stranger_id = stranger.id
    assert (await _profile(client, stranger_id)).status_code == 404, "not a member of varga"

    await _other_organisation()
    await login(client, "eve@other.dev")
    assert (await _profile(client, dev)).status_code == 403


async def test_a_former_member_is_read_by_an_administrator_only(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    dev = await _member(client, "dev@varga.dev")
    await _member(client, "viewer@varga.dev", role="viewer")
    await _work(project["id"], "dev@varga.dev")
    assert (await client.delete(f"{ORG}/members/{dev}")).status_code == 204

    body = (await _profile(client, dev)).json()
    assert body["status"] == "former" and body["org_role"] is None and body["groups"] == []
    assert body["sessions"] is None and body["avatar_url"] is None
    assert [d["work_item_key"] for d in body["work"]["decisions"]] == ["BILL-1"], "their decisions stay"
    assert {e["action"] for e in body["administration"]["audit"]} >= {"member.add", "member.remove"}

    await login(client, "viewer@varga.dev")
    assert (await _profile(client, dev)).status_code == 404
