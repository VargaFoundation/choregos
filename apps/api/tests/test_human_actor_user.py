"""A human step may be addressed to a person (ADR 0153), and an organisation administrator may
reassign a waiting request.

`user` alone: only that person answers (an administrator overrides, audited). `user` and `group`: the
person first, the group once the request's SLA has passed. A reassignment is kept on the request row,
audited with before, after and reason, and the decision check follows it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient

from .kits.human_actor import _decide, _member, _session

GROUP = "release-captains"
PERSON = "lea@varga.dev"


def _workflow(actor: str) -> str:
    return f"""
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: release
  version: 1
initial: waiting
actors:
  captain: {actor}
states:
  waiting: {{display: Waiting for the captain, kind: wait}}
  done: {{display: Done, terminal: true}}
transitions:
  - id: t-ship
    from: waiting
    to: done
    by: captain
"""


PERSON_ONLY = _workflow(f"{{type: human, user: {PERSON}}}")
PERSON_THEN_GROUP = _workflow(f"{{type: human, user: {PERSON}, group: {GROUP}, sla_hours: 4}}")


async def _waiting_ticket(
    client: AsyncClient, pid: str, yaml: str, *, overdue: bool = False
) -> tuple[str, str]:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    assert (
        await client.put(f"/api/v1/projects/{pid}/workflows/release", json={"yaml": yaml})
    ).status_code == 200
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "Ship 1.2", "workflow": "release"}
    )
    assert created.status_code == 201, created.text
    item_id = str(created.json()["id"])
    asked = utcnow() - timedelta(hours=5 if overdue else 0)
    async with session_scope() as session:
        row = HumanRequest(
            work_item_id=item_id,
            project_id=pid,
            transition_id="t-ship",
            kind="approval",
            payload={"summary": "Ship?"},
            requested_at=asked,
            due_at=asked + timedelta(hours=4),
        )
        session.add(row)
        await session.flush()
        request_id = row.id
    return item_id, request_id


async def _audit(action: str) -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars())


# ── a person alone ──


async def test_a_step_addressed_to_a_person_is_answered_by_that_person_only(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    item_id, _ = await _waiting_ticket(client, project["id"], PERSON_ONLY)
    await _member(client, PERSON, "developer", [])
    await _member(client, "cap@varga.dev", "project_owner", [GROUP])
    refused = await _decide(app, "cap@varga.dev", item_id)
    assert refused.status_code == 403 and PERSON in refused.text, refused.text

    shown = (await client.get(f"/api/v1/work-items/{item_id}")).json()["pending_request"]["assignee"]
    assert shown == {"user": PERSON, "group": None, "reassigned": False}

    answered = await _decide(app, PERSON, item_id)
    assert answered.status_code == 202, answered.text
    assert await _audit("workitem.decision.group_override") == [], "the person is not an override"


async def test_the_person_is_matched_without_regard_to_case(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    item_id, _ = await _waiting_ticket(client, project["id"], PERSON_ONLY.replace(PERSON, "Lea@Varga.DEV"))
    await _member(client, PERSON, "developer", [])
    answered = await _decide(app, PERSON, item_id)
    assert answered.status_code == 202, answered.text


async def test_an_administrator_overrides_a_step_addressed_to_a_person_with_an_audit_trail(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id, _ = await _waiting_ticket(client, project["id"], PERSON_ONLY)
    overridden = await client.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})
    assert overridden.status_code == 202, overridden.text
    (entry,) = await _audit("workitem.decision.group_override")
    assert entry.payload["user"] == PERSON and entry.payload["transition"] == "t-ship"


async def test_the_person_still_needs_the_rank_to_decide(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    item_id, _ = await _waiting_ticket(client, project["id"], PERSON_ONLY)
    await _member(client, PERSON, "viewer", [])
    refused = await _decide(app, PERSON, item_id)
    assert refused.status_code == 403, refused.text


# ── a person first, then the group ──


async def test_the_group_waits_for_the_sla_before_answering_a_step_addressed_to_a_person(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    await _member(client, "cap@varga.dev", "developer", [GROUP])
    item_id, _ = await _waiting_ticket(client, project["id"], PERSON_THEN_GROUP)
    early = await _decide(app, "cap@varga.dev", item_id)
    assert early.status_code == 403 and "SLA" in early.text and PERSON in early.text, early.text

    late_id, _ = await _waiting_ticket(client, project["id"], PERSON_THEN_GROUP, overdue=True)
    late = await _decide(app, "cap@varga.dev", late_id)
    assert late.status_code == 202, late.text


async def test_the_person_answers_before_the_sla_and_an_outsider_never(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    await _member(client, PERSON, "developer", [])
    await _member(client, "out@varga.dev", "developer", ["platform-team"])
    item_id, _ = await _waiting_ticket(client, project["id"], PERSON_THEN_GROUP, overdue=True)
    outsider = await _decide(app, "out@varga.dev", item_id)
    assert outsider.status_code == 403 and GROUP in outsider.text, outsider.text
    answered = await _decide(app, PERSON, item_id)
    assert answered.status_code == 202, answered.text


# ── reassignment ──


async def test_only_an_organisation_administrator_reassigns_a_waiting_request(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    item_id, request_id = await _waiting_ticket(client, project["id"], PERSON_ONLY)
    await _member(client, "owner@varga.dev", "project_owner", [])
    owner = await _session(app, "owner@varga.dev")
    try:
        refused = await owner.post(
            f"/api/v1/work-items/{item_id}/human-requests/{request_id}/assignee",
            json={"group": GROUP, "reason": "Léa is away"},
        )
    finally:
        await owner.aclose()
    assert refused.status_code == 403, refused.text
    assert await _audit("workitem.request.reassign") == []
    unknown = await client.post(
        f"/api/v1/work-items/{item_id}/human-requests/nope/assignee", json={"group": GROUP, "reason": "x"}
    )
    assert unknown.status_code == 404, unknown.text


async def test_a_reassigned_request_is_audited_and_answered_by_its_new_assignee(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    item_id, request_id = await _waiting_ticket(client, project["id"], PERSON_ONLY)
    await _member(client, PERSON, "developer", [])
    await _member(client, "max@varga.dev", "developer", [])
    path = f"/api/v1/work-items/{item_id}/human-requests/{request_id}/assignee"

    stranger = await client.post(path, json={"user": "nobody@else.dev", "reason": "x"})
    assert stranger.status_code == 422 and "not a member" in stranger.text, stranger.text
    empty = await client.post(path, json={"reason": "x"})
    assert empty.status_code == 422, empty.text
    silent = await client.post(path, json={"user": PERSON, "reason": "   "})
    assert silent.status_code == 422 and "reason" in silent.text, silent.text

    moved = await client.post(path, json={"user": "Max@varga.dev", "reason": "Léa is on leave"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["assignee"] == {"user": "max@varga.dev", "group": None, "reassigned": True}
    (entry,) = await _audit("workitem.request.reassign")
    assert entry.payload["before"] == {"user": PERSON, "group": None, "reassigned": False}
    assert entry.payload["after"] == {"user": "max@varga.dev", "group": None, "reassigned": True}
    assert entry.payload["reason"] == "Léa is on leave" and entry.payload["request_id"] == request_id

    shown = (await client.get(f"/api/v1/work-items/{item_id}")).json()["pending_request"]["assignee"]
    assert shown["user"] == "max@varga.dev" and shown["reassigned"] is True

    former = await _decide(app, PERSON, item_id)
    assert former.status_code == 403 and "max@varga.dev" in former.text, former.text
    answered = await _decide(app, "max@varga.dev", item_id)
    assert answered.status_code == 202, answered.text

    decided = await client.post(path, json={"group": GROUP, "reason": "too late"})
    assert decided.status_code == 409, decided.text


async def test_a_request_reassigned_to_a_group_is_answered_by_its_members(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    item_id, request_id = await _waiting_ticket(client, project["id"], PERSON_ONLY)
    await _member(client, "cap@varga.dev", "developer", [f"{project['slug']}-captains"])
    moved = await client.post(
        f"/api/v1/work-items/{item_id}/human-requests/{request_id}/assignee",
        json={"group": "{project}-captains", "reason": "the captains take it"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["assignee"]["group"] == f"{project['slug']}-captains"
    answered = await _decide(app, "cap@varga.dev", item_id)
    assert answered.status_code == 202, answered.text


# ── validation ──


async def test_a_person_outside_the_organisation_is_warned_about_at_validation(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    path = f"/api/v1/projects/{project['id']}/workflows/validate"
    unknown = (await client.post(path, json={"yaml": PERSON_ONLY})).json()
    assert unknown["valid"] is True
    assert [w["code"] for w in unknown["warnings"]] == ["actor.user_unknown"]
    assert PERSON in unknown["warnings"][0]["message"]
    assert unknown["warnings"][0]["path"] == "/actors/captain/user"

    await _member(client, PERSON, "developer", [])
    known = (await client.post(path, json={"yaml": PERSON_ONLY})).json()
    assert known["warnings"] == []


async def test_a_human_actor_naming_nobody_is_refused(client: AsyncClient) -> None:
    nobody = _workflow("{type: human, sla_hours: 4}")
    refused = (await client.post("/api/v1/workflows/validate", json={"yaml": nobody})).json()
    assert refused["valid"] is False
