"""A person's client prepares their decision at the MCP door; the person decides (S65-21, ADR 0163).

`get_pending_decision` reads the caller's own waiting requests, `draft_decision` deposits a draft on one.
A draft decides nothing: the request keeps waiting, no decision is written or delivered. Another person's
request answers like one that does not exist. The draft needs `mcp:write` and `item:decide`, counts in the
day's writes and is audited; the person discards it, or decides, and the decision's audit says whether it
was the draft's.
"""

from __future__ import annotations

from typing import Any

from httpx import ASGITransport, AsyncClient

from .kits.human_actor import _member, _session

PERSON = "lea@varga.dev"
OTHER = "max@varga.dev"
TASK_FORM = {
    "type": "object",
    "properties": {"amount": {"type": "integer", "minimum": 0}, "comment": {"type": "string"}},
    "required": ["amount", "comment"],
}


async def _request(
    client: AsyncClient, pid: str, title: str, *, user: str, kind: str = "approval", **payload: Any
) -> tuple[str, str, str]:
    """A work item waiting for `user` (an administrator's reassignment names them, ADR 0153)."""
    from choregos_api.db.models import HumanRequest, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    created = await client.post(f"/api/v1/projects/{pid}/work-items", json={"title": title, "start": False})
    assert created.status_code == 201, created.text
    item_id = str(created.json()["id"])
    async with session_scope() as session:
        item = await session.get(WorkItem, item_id)
        assert item is not None
        row = HumanRequest(
            work_item_id=item_id,
            project_id=pid,
            kind=kind,
            payload={"question": f"{title}?", **payload},
            requested_at=utcnow(),
            assignee={"user": user, "group": None},
        )
        session.add(row)
        await session.flush()
        return item_id, item.tracker_key, row.id


async def _token(app: Any, email: str, *scopes: str) -> str:
    person = await _session(app, email)
    try:
        created = await person.post("/api/v1/me/tokens", json={"name": "claude-code", "scopes": list(scopes)})
        assert created.status_code == 201, created.text
        return str(created.json()["token"])
    finally:
        await person.aclose()


async def _rpc(app: Any, token: str, method: str, params: dict[str, Any] | None = None) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as door:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        response = await door.post(
            "/mcp",
            json=message,
            headers={"Authorization": f"Bearer {token}", "User-Agent": "claude-code/2.1"},
        )
        assert response.status_code == 200, response.text
        return response.json()


async def _tool(app: Any, token: str, name: str, **arguments: Any) -> Any:
    return (await _rpc(app, token, "tools/call", {"name": name, "arguments": arguments}))["result"]


async def _row(request_id: str) -> Any:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return await session.get(HumanRequest, request_id)


async def _audit(action: str) -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars())


async def _decided_events() -> int:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import func, select

    async with session_scope() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(Event)
                    .where(Event.type == EventType.WORKITEM_HUMAN_DECIDED.value)
                )
            ).scalar_one()
        )


async def _setup(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    mine = await _request(client, project["id"], "Ship 1.2", user=PERSON)
    theirs = await _request(client, project["id"], "Ship 2.0", user=OTHER)
    await _member(client, PERSON, "developer", [])
    await _member(client, OTHER, "developer", [])
    return {"mine": mine, "theirs": theirs}


async def test_the_draft_tool_needs_write_and_the_rank_to_decide(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    await _setup(client, project)
    reader = await _token(app, PERSON, "mcp:read")
    writer = await _token(app, PERSON, "mcp:write")
    read_names = {t["name"] for t in (await _rpc(app, reader, "tools/list"))["result"]["tools"]}
    write_names = {t["name"] for t in (await _rpc(app, writer, "tools/list"))["result"]["tools"]}
    assert "get_pending_decision" in read_names and "draft_decision" not in read_names
    assert {"get_pending_decision", "draft_decision"} <= write_names
    refused = await _rpc(app, reader, "tools/call", {"name": "draft_decision", "arguments": {}})
    assert refused["error"]["code"] == -32602, "a tool not announced does not exist"

    await _member(client, "viewer@varga.dev", "viewer", [])
    viewer = await _token(app, "viewer@varga.dev", "mcp:write")
    viewer_names = {t["name"] for t in (await _rpc(app, viewer, "tools/list"))["result"]["tools"]}
    assert "draft_decision" not in viewer_names, "a person who cannot decide gets no draft tool"


async def test_a_person_reads_only_their_own_waiting_requests(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    made = await _setup(client, project)
    token = await _token(app, PERSON, "mcp:read")
    listed = (await _tool(app, token, "get_pending_decision"))["structuredContent"]["pending"]
    assert [p["key"] for p in listed] == [made["mine"][1]]
    assert listed[0]["decision_url"].endswith(f"/items/{made['mine'][0]}#decision")

    one = (await _tool(app, token, "get_pending_decision", work_item=made["mine"][1]))["structuredContent"]
    assert one["request_id"] == made["mine"][2] and one["question"] == "Ship 1.2?"
    assert one["draft_kinds"] == ["approve", "reject"] and one["assignee"]["user"] == PERSON

    foreign = await _tool(app, token, "get_pending_decision", work_item=made["theirs"][1])
    missing = await _tool(app, token, "get_pending_decision", work_item="NOPE-1")
    assert foreign["isError"] and missing["isError"]
    assert foreign["content"][0]["text"].replace(made["theirs"][1], "X") == missing["content"][0][
        "text"
    ].replace("NOPE-1", "X"), "another person's request answers like one that does not exist"


async def test_a_draft_decides_nothing_and_is_counted_and_audited(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    from choregos_api.db.models import Event, User
    from choregos_api.db.session import session_scope
    from choregos_api.mcp.limits import writes_today
    from choregos_contracts import EventType
    from sqlalchemy import select

    made = await _setup(client, project)
    item_id, key, request_id = made["mine"]
    token = await _token(app, PERSON, "mcp:write")
    result = await _tool(
        app, token, "draft_decision", work_item=key, kind="approve", note="the checks are green"
    )
    assert not result.get("isError"), result
    structured = result["structuredContent"]
    assert "NOTHING was decided" in result["content"][0]["text"]
    (pending,) = structured["pending_actions"]
    assert pending["id"] == request_id and pending["confirm_url"].endswith(f"/items/{item_id}#decision")

    row = await _row(request_id)
    assert row.decided_at is None and row.decision is None, "the request still waits for the person"
    assert row.draft["kind"] == "approve" and row.draft["by"] == PERSON
    assert row.draft["via"] == {
        "door": "mcp",
        "client": "claude-code",
        "agent": None,
        "user_agent": "claude-code/2.1",
    }
    assert await _audit("workitem.decision") == []
    async with session_scope() as session:
        decided = (
            await session.execute(select(Event).where(Event.type == EventType.WORKITEM_HUMAN_DECIDED.value))
        ).scalars()
        assert list(decided) == [], "nothing reached the workflow"
        user = (await session.execute(select(User).where(User.email == PERSON))).scalar_one()
        assert await writes_today(session, user.id) == 1, "a draft counts in the day's writes"
    (entry,) = await _audit("workitem.decision.draft")
    assert entry.payload["request_id"] == request_id and entry.payload["kind"] == "approve"

    shown = (await client.get(f"/api/v1/work-items/{item_id}")).json()["pending_request"]["draft"]
    assert shown["kind"] == "approve" and shown["note"] == "the checks are green"

    foreign = await _tool(app, token, "draft_decision", work_item=made["theirs"][1], kind="approve")
    assert foreign["isError"] and (await _row(made["theirs"][2])).draft is None


async def test_a_draft_fits_its_request(client: AsyncClient, project: dict[str, Any], app: Any) -> None:
    made = await _setup(client, project)
    _, task_key, task_id = await _request(
        client, project["id"], "Pay", user=PERSON, kind="task", form=TASK_FORM, attest="I checked the invoice"
    )
    token = await _token(app, PERSON, "mcp:write")
    key = made["mine"][1]
    wrong = await _tool(app, token, "draft_decision", work_item=key, kind="answer", answer="yes")
    assert wrong["isError"] and "`approve` or `reject`" in wrong["content"][0]["text"]
    outside = await _tool(
        app, token, "draft_decision", work_item=task_key, kind="complete", values={"iban": "x"}
    )
    assert outside["isError"] and "iban" in outside["content"][0]["text"]
    invalid = await _tool(
        app, token, "draft_decision", work_item=task_key, kind="complete", values={"amount": -1}
    )
    assert invalid["isError"] and "amount" in invalid["content"][0]["text"]
    partial = await _tool(
        app, token, "draft_decision", work_item=task_key, kind="complete", values={"amount": 40}
    )
    assert not partial.get("isError"), partial
    draft = (await _row(task_id)).draft
    assert draft["values"] == {"amount": 40} and "attested" not in draft, "a draft never attests"
    again = await _tool(app, token, "draft_decision", work_item=task_key, kind="reject", reason="no invoice")
    assert not again.get("isError")
    assert (await _row(task_id)).draft["kind"] == "reject", "a new draft replaces the previous one"


async def test_the_person_discards_a_draft_or_decides_and_the_audit_says_which(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    made = await _setup(client, project)
    item_id, key, request_id = made["mine"]
    token = await _token(app, PERSON, "mcp:write")
    await _tool(app, token, "draft_decision", work_item=key, kind="approve")
    path = f"/api/v1/work-items/{item_id}/human-requests/{request_id}/draft"

    other = await _session(app, OTHER)
    person = await _session(app, PERSON)
    try:
        assert (await other.delete(path)).status_code == 403, "only who may answer discards it"
        assert (await person.delete(path)).status_code == 204
        assert (await _row(request_id)).draft is None
        assert (await person.delete(path)).status_code == 404
        (discarded,) = await _audit("workitem.decision.draft.discard")
        assert discarded.payload["drafted_by"] == PERSON and discarded.payload["kind"] == "approve"

        await _tool(app, token, "draft_decision", work_item=key, kind="approve")
        decided = await person.post(f"/api/v1/work-items/{item_id}/decisions", json={"kind": "approve"})
        assert decided.status_code == 202, decided.text
        (decision,) = await _audit("workitem.decision")
        assert decision.payload["draft"] == "same"
        assert await _decided_events() == 1, "the person's decision, and only it, reached the workflow"
        assert (await person.delete(path)).status_code == 409, "a decided request keeps what was proposed"
        late = await _tool(app, token, "draft_decision", work_item=key, kind="reject")
        assert late["isError"], "a decided request takes no draft"
    finally:
        await other.aclose()
        await person.aclose()


def test_the_comparison_of_a_decision_with_its_draft() -> None:
    from choregos_api.services.decision_drafts import compared

    assert compared(None, "approve", {}) is None
    assert compared({"kind": "approve"}, "approve", {}) == "same"
    assert compared({"kind": "approve"}, "reject", {}) == "changed"
    assert compared({"kind": "answer", "answer": "yes"}, "answer", {"answer": "no"}) == "changed"
    assert compared({"kind": "complete", "values": {"a": 1}}, "complete", {"values": {"a": 1}}) == "same"
