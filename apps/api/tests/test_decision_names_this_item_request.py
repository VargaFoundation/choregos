"""A decision answers a request of the work item it is posted on (S67-15, the audit of the senders).

`postDecision` takes an optional `request_id`. It used to look the request up by that id alone: a person
allowed to decide on one work item could close, and answer for, a request of another one (the same project
or another), which the other item's interpreter never received.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_api.decision_session import CODE
from tranibor_api.errors import ApiError
from tranibor_api.rbac import Principal

from .test_human_task import _waiting_work_item

EXPECTED_RUN_WRITES: set[tuple[str, str]] = {
    ("POST", "/runs/{id}/context-breakdown"),
    ("POST", "/runs/{id}/egress-token"),
    ("POST", "/runs/{id}/events"),
    ("POST", "/runs/{id}/findings"),
    ("POST", "/runs/{id}/git-token"),
    ("POST", "/runs/{id}/knowledge/read"),
    ("POST", "/runs/{id}/knowledge/search"),
    ("POST", "/runs/{id}/memory/search"),
    ("POST", "/runs/{id}/network/decisions"),
    ("POST", "/runs/{id}/question"),
    ("POST", "/runs/{id}/result"),
    ("POST", "/runs/{id}/scope-change"),
    ("POST", "/runs/{id}/tools/{name}"),
    ("PUT", "/runs/{id}/outputs/{name}"),
}


async def _decided_at(request_id: str) -> Any:
    from tranibor_api.db.models import HumanRequest
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        row = await session.get(HumanRequest, request_id)
        assert row is not None
        return row.decided_at


async def _request_of(item_id: str) -> str:
    from sqlalchemy import select
    from tranibor_api.db.models import HumanRequest
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        return (
            await session.execute(select(HumanRequest.id).where(HumanRequest.work_item_id == item_id))
        ).scalar_one()


async def test_a_decision_names_a_request_of_its_own_work_item(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    mine = await _waiting_work_item(client, project["id"], kind="approval")
    other = await _waiting_work_item(client, project["id"], kind="approval")
    theirs = await _request_of(other["id"])

    refused = await client.post(
        f"/api/v1/work-items/{mine['id']}/decisions", json={"kind": "approve", "request_id": theirs}
    )

    assert refused.status_code == 409, refused.text
    assert await _decided_at(theirs) is None, "the other item's request is still waiting"
    ok = await client.post(
        f"/api/v1/work-items/{other['id']}/decisions", json={"kind": "approve", "request_id": theirs}
    )
    assert ok.status_code == 202, ok.text


# ───────────────────────── the other senders, audited (S67-15) ─────────────────────────


async def test_an_outposts_signature_decides_nothing_on_any_decision_route(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Outposts (ADR 0147 §3): a request carrying an instance's signature is refused before any session is
    read, on every route that decides or settles, whoever the session is (here the organisation's
    administrator), and the request it aimed at keeps waiting."""
    from tranibor_contracts import SIGNATURE_HEADER

    ticket = await _waiting_work_item(client, project["id"], kind="approval")
    request_id = await _request_of(ticket["id"])
    pid = project["id"]
    routes = [
        (f"/api/v1/work-items/{ticket['id']}/decisions", {"kind": "approve"}),
        (f"/api/v1/projects/{pid}/actions/a1/decision", {"decision": "approve"}),
        (f"/api/v1/projects/{pid}/actions/a1/settlement", {"outcome": "made", "note": "found it"}),
        ("/api/v1/releases/r1/approve", {"note": ""}),
        ("/api/v1/me/conversations/c1/actions/a1/confirm", None),
        ("/api/v1/me/conversations/c1/actions/a1/refuse", {"reason": "no"}),
    ]
    signed = {SIGNATURE_HEADER: "0" * 64}
    for path, body in routes:
        refused = await client.post(path, json=body, headers=signed)
        assert refused.status_code == 403, f"{path}: {refused.text}"
        assert "outposts' door only" in refused.json()["detail"], path
    assert await _decided_at(request_id) is None


@pytest.mark.parametrize("kind", ["outpost", "system", "machine", "run"])
async def test_no_other_kind_of_principal_decides_even_when_signed_in_fresh(kind: str) -> None:
    """An outpost, the system, a machine, a run: none is a person, whatever credential it carries."""
    from tranibor_api.decision_session import holds_a_person_session
    from tranibor_api.schemas.actions import ActionSettlementBody
    from tranibor_api.services import actions
    from tranibor_core import utcnow

    principal = Principal(
        user_id="x",
        email="x@b.test",
        kind=kind,
        credential="cookie",
        authenticated_at=int(utcnow().timestamp()),
    )
    assert not holds_a_person_session(principal)
    with pytest.raises(ApiError) as decision:
        await actions.decide(None, None, "northwind", "a1", "approve", None, principal)  # type: ignore[arg-type]
    with pytest.raises(ApiError) as settlement:
        body = ActionSettlementBody(outcome="made", note="found it")
        await actions.settle(None, None, "northwind", "a1", body, principal)  # type: ignore[arg-type]
    assert decision.value.extra == settlement.value.extra == {"code": CODE}


def test_a_run_token_reaches_no_route_that_decides() -> None:
    """The run token's API (`/internal`) writes what is listed here and nothing else. A new write must be
    added to the list on purpose, after asking whether it can answer a human request or settle an action:
    `scope-change` and `question` open a request, `network/decisions` is a journal of what the proxy let
    through, none of them answers one."""
    from tranibor_api.routers.internal import router

    writes = {
        (method, route.path.removeprefix(router.prefix))
        for route in router.routes
        for method in getattr(route, "methods", set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert writes == EXPECTED_RUN_WRITES, writes ^ EXPECTED_RUN_WRITES
