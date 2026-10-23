"""The A2A door (ADR 0078): governed workflows served as A2A skills to outside agents.

The caller simulated here is another agent holding a person's `a2a` token. Each guard of the
door has its test: the scope, the refusal of `*`, the opt-in, the derived task id, the decision
that stays in the console, the rate and the parts cap; a stream's end, its credential read again,
and the cap on open streams.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient, Response

CARD = "/a2a/orgs/varga/.well-known/agent-card.json"
ENDPOINT = "/a2a/orgs/varga"
OPT_IN = """
a2a:
  skill: ship-a-change
  description: Ship a change through review and release.
"""


async def _token(client: AsyncClient, *scopes: str, project: str | None = None) -> str:
    body: dict[str, Any] = {"name": "partner-agent", "scopes": list(scopes)}
    if project:
        body["project"] = project
    created = await client.post("/api/v1/me/tokens", json=body)
    assert created.status_code == 201, created.text
    return str(created.json()["token"])


async def _publish(
    client: AsyncClient, project: dict[str, Any], *, opt_in: bool = True, modes: list[str] | None = None
) -> None:
    from choregos_core.dsl import template_yaml

    tracker = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    block = OPT_IN + (f"  input_modes: [{', '.join(modes)}]\n" if modes else "")
    text = template_yaml("default-simple").rstrip("\n") + "\n" + (block if opt_in else "")
    published = await client.put(f"/api/v1/projects/{project['id']}/workflow", json={"yaml": text})
    assert published.status_code == 200, published.text


async def _rpc(client: AsyncClient, token: str, method: str, params: dict[str, Any]) -> Response:
    return await client.post(
        ENDPOINT,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        headers={"Authorization": f"Bearer {token}"},
    )


def _message(text: str, message_id: str = "m-1", **extra: Any) -> dict[str, Any]:
    return {
        "message": {
            "kind": "message",
            "role": "user",
            "messageId": message_id,
            "parts": [{"kind": "text", "text": text}],
            **extra,
        }
    }


async def _send(client: AsyncClient, token: str, text: str = "Add credit notes", **kw: Any) -> dict[str, Any]:
    answer = await _rpc(client, token, "message/send", _message(text, **kw))
    assert answer.status_code == 200, answer.text
    assert "result" in answer.json(), answer.text
    return dict(answer.json()["result"])


async def _ready(
    client: AsyncClient, project: dict[str, Any], *, opt_in: bool = True, modes: list[str] | None = None
) -> str:
    await _publish(client, project, opt_in=opt_in, modes=modes)
    token = await _token(client, "a2a")
    client.cookies.clear()
    return token


# ───────────────────────────── who may knock ─────────────────────────────


async def test_without_a_token_a_401_says_how_to_authenticate(client: AsyncClient, admin: str) -> None:
    answer = await client.get(CARD)
    assert answer.status_code == 401, answer.text
    assert answer.headers["www-authenticate"].startswith("Bearer")
    assert "a2a" in answer.json()["error"]


async def test_the_door_needs_the_a2a_scope(client: AsyncClient, admin: str) -> None:
    """An MCP token opens the MCP door, not this one."""
    token = await _token(client, "mcp:write")
    client.cookies.clear()
    for answer in (await client.get(CARD, headers={"Authorization": f"Bearer {token}"}),
                   await _rpc(client, token, "tasks/get", {"id": "x"})):  # fmt: skip
        assert answer.status_code == 403, answer.text
        assert 'error="insufficient_scope"' in answer.headers["www-authenticate"]


async def test_a_full_api_token_is_refused(client: AsyncClient, admin: str) -> None:
    """A `*` token opens the REST API: it does not belong in another agent's configuration."""
    token = await _token(client, "*")
    client.cookies.clear()
    answer = await client.get(CARD, headers={"Authorization": f"Bearer {token}"})
    assert answer.status_code == 403, answer.text
    assert 'error="insufficient_scope"' in answer.headers["www-authenticate"]


async def test_an_a2a_token_does_not_open_the_rest_api(client: AsyncClient, admin: str) -> None:
    token = await _token(client, "a2a")
    client.cookies.clear()
    answer = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert answer.status_code == 403, answer.text


async def test_an_unknown_organisation_is_not_found(client: AsyncClient, admin: str) -> None:
    token = await _token(client, "a2a")
    client.cookies.clear()
    answer = await client.get(
        "/a2a/orgs/elsewhere/.well-known/agent-card.json", headers={"Authorization": f"Bearer {token}"}
    )
    assert answer.status_code == 404, answer.text


# ───────────────────────────── the card ─────────────────────────────


async def test_a_workflow_that_did_not_opt_in_is_not_a_skill(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    token = await _ready(client, project, opt_in=False)
    card = (await client.get(CARD, headers={"Authorization": f"Bearer {token}"})).json()
    assert card["skills"] == []
    refused = (await _rpc(client, token, "message/send", _message("hello"))).json()
    assert refused["error"]["code"] == -32602, refused


async def test_the_card_lists_the_opted_in_workflow_and_parses_as_a2a(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_adapters.a2a import AUTH_BEARER, parse_card

    token = await _ready(client, project)
    raw = (await client.get(CARD, headers={"Authorization": f"Bearer {token}"})).json()
    card = parse_card(raw)
    assert card.protocol_version == "0.3.0"
    assert card.url.endswith("/a2a/orgs/varga")
    assert [s.id for s in card.skills] == ["billing-api/ship-a-change"]
    assert AUTH_BEARER in card.auth_types
    assert raw["capabilities"]["streaming"] is True
    assert raw["capabilities"]["pushNotifications"] is True


# ───────────────────────────── message/send ─────────────────────────────


async def test_a_resent_message_is_the_same_task(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import AuditLog, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    token = await _ready(client, project)
    first = await _send(client, token, message_id="m-42")
    again = await _send(client, token, message_id="m-42")
    other = await _send(client, token, message_id="m-43")
    assert first["id"] == again["id"] != other["id"]
    assert first["status"]["state"] == "working"
    assert first["metadata"]["choregos"]["project"] == "varga:billing-api"
    async with session_scope() as session:
        items = list((await session.execute(select(WorkItem))).scalars())
        calls = list((await session.execute(select(AuditLog).where(AuditLog.action == "a2a.call"))).scalars())
        created = list(
            (await session.execute(select(AuditLog).where(AuditLog.action == "workitem.create"))).scalars()
        )
    assert sorted(i.id for i in items) == sorted([first["id"], other["id"]])
    assert [i.title for i in items] == ["Add credit notes"] * 2
    assert all(c.payload["canal"] == "a2a" for c in created)
    assert all((i.initiated_by or "").startswith("a2a:") and len(i.initiated_by or "") > 4 for i in items), [
        i.initiated_by for i in items
    ]
    assert len(calls) == 3
    assert [c.payload["ecriture"] for c in calls].count(True) == 2, "a resent message writes nothing"


async def test_tasks_get_reads_the_work_item(client: AsyncClient, project: dict[str, Any]) -> None:
    token = await _ready(client, project)
    task = await _send(client, token)
    read = (await _rpc(client, token, "tasks/get", {"id": task["id"]})).json()["result"]
    assert read["id"] == task["id"]
    assert read["status"]["state"] == "working"
    unknown = (await _rpc(client, token, "tasks/get", {"id": "nope"})).json()
    assert unknown["error"]["code"] == -32001


async def test_the_parts_are_capped(client: AsyncClient, project: dict[str, Any]) -> None:
    token = await _ready(client, project)
    many = _message("x")
    many["message"]["parts"] = [{"kind": "text", "text": "x"}] * 21
    assert (await _rpc(client, token, "message/send", many)).json()["error"]["code"] == -32602
    long = (await _rpc(client, token, "message/send", _message("y" * 100_001))).json()
    assert long["error"]["code"] == -32602
    with_file = _message("x")
    with_file["message"]["parts"].append(
        {"kind": "file", "file": {"name": "a", "mimeType": "application/pdf", "bytes": "eA=="}}
    )
    refused = (await _rpc(client, token, "message/send", with_file)).json()
    assert refused["error"]["code"] == -32005, "the skill does not list application/pdf"
    data = _message("x")
    data["message"]["parts"].append({"kind": "data", "data": {"k": 1}})
    refused = (await _rpc(client, token, "message/send", data)).json()
    assert refused["error"]["code"] == -32005, "the skill takes text/plain only"


# ───────────────────────────── file parts (ADR 0078 §6) ─────────────────────────────

PDF = b"%PDF-1.7 a signed contract"
FILE_MODES = ["text/plain", "application/pdf"]


def _file(name: str | None, data: bytes, mime: str | None = "application/pdf") -> dict[str, Any]:
    import base64

    file: dict[str, Any] = {"bytes": base64.b64encode(data).decode()}
    if name is not None:
        file["name"] = name
    if mime is not None:
        file["mimeType"] = mime
    return {"kind": "file", "file": file}


def _with_files(*parts: dict[str, Any], message_id: str = "m-f") -> dict[str, Any]:
    message = _message("Review the contract", message_id=message_id)
    message["message"]["parts"].extend(parts)
    return message


@pytest.fixture
def store() -> Any:
    from choregos_adapters.fakes import FakeObjectStore
    from choregos_api.object_store import use_object_store

    fake = FakeObjectStore()
    use_object_store(fake)
    yield fake
    use_object_store(None)


async def test_a_file_part_is_an_input_artefact_a_step_reads(
    client: AsyncClient, project: dict[str, Any], store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.a2a import door
    from choregos_api.db.models import ArtifactRecord, AuditLog, Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_api.temporal import get_temporal
    from choregos_contracts import artifact_digest
    from sqlalchemy import select

    token = await _ready(client, project, modes=FILE_MODES)
    order: list[str] = []
    real_start, real_record = door.start_work_item, door.record_input_file

    async def start(owner: Any, item: Any) -> None:
        order.append("start")
        await real_start(owner, item)

    async def record_file(session: Any, **kw: Any) -> Any:
        order.append(f"record {kw['stored'].name}")
        return await real_record(session, **kw)

    monkeypatch.setattr(door, "start_work_item", start)
    monkeypatch.setattr("choregos_api.services.tickets.start_work_item", start)
    monkeypatch.setattr(door, "record_input_file", record_file)
    answer = (await _rpc(client, token, "message/send", _with_files(_file("contract.pdf", PDF)))).json()
    task = answer["result"]
    assert order == ["record contract.pdf", "start"], "the interpreter starts once the file is recorded"
    assert any(task["id"] in str(payload) for payload in get_temporal().started.values())  # type: ignore[attr-defined]
    again = (await _rpc(client, token, "message/send", _with_files(_file("contract.pdf", PDF)))).json()
    assert again["result"]["id"] == task["id"]
    assert store.puts == 1, "a resent message stores nothing"

    async with session_scope() as session:
        rows = list((await session.execute(select(ArtifactRecord))).scalars())
        audits = list(
            (
                await session.execute(select(AuditLog).where(AuditLog.action == "workitem.input_file"))
            ).scalars()
        )
        item = await session.get(WorkItem, task["id"])
        assert item is not None
        assert "`contract.pdf` (application/pdf" in (item.body_snapshot or "")
        session.add(
            Run(
                id="run-review",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="review",
                status="running",
                stage_input={"transition": {"outputs": [], "inputs": ["contract.pdf"]}},
            )
        )
        key = item.tracker_key
    assert [(r.name, r.type, r.media_type, r.digest) for r in rows] == [
        ("contract.pdf", "pdf", "application/pdf", artifact_digest(PDF))
    ]
    assert rows[0].run_id == f"a2a:{task['id']}" and rows[0].storage_key in store.objects
    assert [(a.payload["name"], a.payload["size"]) for a in audits] == [("contract.pdf", len(PDF))]

    headers = {
        "Authorization": "Bearer "
        + mint_run_token("run-review", project_slug="billing-api", work_item_key=key, ttl_minutes=5)
    }
    listed = (await client.get("/api/v1/internal/runs/run-review/inputs", headers=headers)).json()
    assert [(f["name"], f["filename"], f["digest"]) for f in listed] == [
        ("contract.pdf", "contract.pdf", artifact_digest(PDF))
    ]
    read = await client.get("/api/v1/internal/runs/run-review/inputs/contract.pdf", headers=headers)
    assert read.status_code == 200 and read.content == PDF
    from choregos_api.services.artifacts import producing_runs

    async with session_scope() as session:
        sources = await producing_runs(session, task["id"], ["contract.pdf"], {}, reader="run-review")
    assert sources == [], "a file the caller sent is no run of the chain"

    async with session_scope() as session:
        done = await session.get(WorkItem, task["id"])
        owner = await session.get(Project, project["id"])
        assert done is not None and owner is not None
        done.state = "deployed_prod"
    completed = (await _rpc(client, token, "tasks/get", {"id": task["id"]})).json()["result"]
    assert completed["status"]["state"] == "completed"
    assert "contract.pdf" not in [a["name"] for a in completed.get("artifacts", [])], "not sent back"


async def test_file_parts_are_bounded_by_the_project(
    client: AsyncClient, project: dict[str, Any], store: Any
) -> None:
    from choregos_api.db.models import ArtifactRecord, Project, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    token = await _ready(client, project, modes=FILE_MODES)
    async with session_scope() as session:
        owner = await session.get(Project, project["id"])
        assert owner is not None
        owner.config = {**owner.config, "a2a": {"max_file_bytes": 10}}

    async def code(*parts: dict[str, Any]) -> tuple[int, str]:
        error = (await _rpc(client, token, "message/send", _with_files(*parts))).json()["error"]
        return error["code"], error["message"]

    assert await code(_file("big.pdf", b"x" * 11)) == (
        -32602,
        "file `big.pdf` too large: 10 bytes at most for this project",
    )
    assert (await code(_file("a.pdf", b"x" * 6), _file("b.pdf", b"y" * 6)))[1].endswith(
        "10 bytes at most together for this project"
    )
    uri = {"kind": "file", "file": {"uri": "https://elsewhere.example/c.pdf", "mimeType": "application/pdf"}}
    assert (await code(uri))[0] == -32602 and "`uri` is not fetched" in (await code(uri))[1]
    assert (await code(_file("p.png", b"png", mime="image/png")))[0] == -32005, (
        "not in the skill's input modes"
    )
    assert "names its `mimeType`" in (await code(_file("c.pdf", b"x", mime=None)))[1]
    assert (
        "not base64"
        in (
            await code(
                {"kind": "file", "file": {"name": "c.pdf", "mimeType": "application/pdf", "bytes": "%%%"}}
            )
        )[1]
    )
    assert "file name refused" in (await code(_file("../c.pdf", b"x")))[1]
    assert "two files named" in (await code(_file("c.pdf", b"x"), _file("c.pdf", b"y")))[1]

    store.fail_next()
    assert (await code(_file("c.pdf", b"x")))[0] == -32603, "a store that fails creates nothing"
    async with session_scope() as session:
        assert not list((await session.execute(select(WorkItem))).scalars())
        assert not list((await session.execute(select(ArtifactRecord))).scalars())
    assert not store.objects

    named = (await _rpc(client, token, "message/send", _with_files(_file(None, b"x")))).json()["result"]
    async with session_scope() as session:
        names = [r.name for r in (await session.execute(select(ArtifactRecord))).scalars()]
    assert named["id"] and names == ["attachment-1"], "a file without a name gets one"


async def test_the_door_reads_a_body_only_as_large_as_the_organisation_takes(
    client: AsyncClient, project: dict[str, Any], store: Any
) -> None:
    from choregos_api.a2a.door import MAX_BODY
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.mcp.transport import TAILLE_MAX_DU_CORPS

    token = await _ready(client, project, modes=FILE_MODES)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    too_large = await client.post(
        ENDPOINT, content=b"{}", headers={**headers, "Content-Length": str(MAX_BODY + 1)}
    )
    assert too_large.status_code == 413
    async with session_scope() as session:
        owner = await session.get(Project, project["id"])
        assert owner is not None
        owner.config = {**owner.config, "a2a": {"max_file_bytes": 10}}
    body = b" " * (TAILLE_MAX_DU_CORPS + 17)
    refused = await client.post(ENDPOINT, content=body, headers=headers)
    assert refused.status_code == 413 and "for this organisation" in refused.text


# ───────────────────────────── no decision over A2A ─────────────────────────────


async def test_a_pending_decision_is_input_required_and_stays_in_the_console(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import get_temporal
    from choregos_core import utcnow

    token = await _ready(client, project)
    task = await _send(client, token)
    async with session_scope() as session:
        session.add(
            HumanRequest(
                work_item_id=task["id"],
                project_id=project["id"],
                kind="approval",
                payload={"question": "Merge?"},
                requested_at=utcnow(),
            )
        )
    read = (await _rpc(client, token, "tasks/get", {"id": task["id"]})).json()["result"]
    assert read["status"]["state"] == "input-required"
    data = next(p["data"] for p in read["status"]["message"]["parts"] if p["kind"] == "data")
    assert data["decision_url"].endswith(f"/p/billing-api/items/{task['id']}")
    assert data["kind"] == "approval"

    reply = _message("approve", message_id="m-2", taskId=task["id"])
    refused = (await _rpc(client, token, "message/send", reply)).json()
    assert refused["error"]["code"] == -32004, refused
    assert refused["error"]["data"]["decision_url"] == data["decision_url"]
    async with session_scope() as session:
        from sqlalchemy import select

        pending = (await session.execute(select(HumanRequest))).scalar_one()
    assert pending.decided_at is None
    assert not [s for s in get_temporal().signals if s[1] == "human_decision"]  # type: ignore[attr-defined]


async def test_states_map_one_way(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import ArtifactRecord, Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    token = await _ready(client, project)
    ids = [(await _send(client, token, message_id=f"m-{n}"))["id"] for n in range(3)]
    async with session_scope() as session:
        done, failed, closed = [await session.get(WorkItem, i) for i in ids]
        assert done and failed and closed
        done.state = "deployed_prod"
        failed.failure = {"reason": "boom"}
        closed.closed_at = utcnow()
        owner = await session.get(Project, project["id"])
        assert owner is not None
        session.add(
            ArtifactRecord(
                org_id=owner.org_id,
                work_item_id=done.id,
                name="release-notes",
                version=1,
                type="markdown",
                kind="inline",
                media_type="text/markdown",
                digest="sha256:" + "0" * 64,
                size=3,
                content="ok",
                run_id="run-1",
            )
        )
    states = [(await _rpc(client, token, "tasks/get", {"id": i})).json()["result"] for i in ids]
    assert [s["status"]["state"] for s in states] == ["completed", "failed", "canceled"]
    assert [a["name"] for a in states[0]["artifacts"]] == ["release-notes"]


async def test_a_completed_task_carries_its_artefacts_content(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Small artefacts travel in parts, checked against their digest; a large one stays in the console."""
    import base64

    from choregos_api.a2a.door import MAX_ARTIFACT_INLINE
    from choregos_api.db.models import ArtifactRecord, Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.object_store import object_store
    from choregos_contracts import artifact_digest, inline_bytes

    token = await _ready(client, project)
    task_id = (await _send(client, token))["id"]
    pdf, tampered, large = b"%PDF-1.7 notes", b"original", b"x" * (MAX_ARTIFACT_INLINE + 1)
    store = object_store()
    async with session_scope() as session:
        done = await session.get(WorkItem, task_id)
        owner = await session.get(Project, project["id"])
        assert done and owner
        done.state = "deployed_prod"

        def row(name: str, type_: str, media: str, data: bytes, **where: Any) -> ArtifactRecord:
            kind = "inline" if "content" in where else "file"
            return ArtifactRecord(
                org_id=owner.org_id, work_item_id=done.id, name=name, version=1, type=type_, kind=kind,
                media_type=media, digest=artifact_digest(data), size=len(data), run_id="run-1", **where,
            )  # fmt: skip

        for key, data in (("k/pdf", pdf), ("k/tampered", tampered), ("k/large", large)):
            await store.put(key, data, "application/octet-stream")
        store.tamper("k/tampered", b"rewritten")  # type: ignore[attr-defined]
        session.add_all(
            [
                row("notes", "markdown", "text/markdown", inline_bytes("Shipped."), content="Shipped."),
                row("summary", "json", "application/json", inline_bytes({"ok": 1}), content={"ok": 1}),
                row("deck", "pdf", "application/pdf", pdf, storage_key="k/pdf"),
                row("tampered", "pdf", "application/pdf", tampered, storage_key="k/tampered"),
                row("large", "pdf", "application/pdf", large, storage_key="k/large"),
            ]
        )
    task = (await _rpc(client, token, "tasks/get", {"id": task_id})).json()["result"]
    parts = {a["name"]: a["parts"][0] for a in task["artifacts"]}
    assert parts["notes"] == {"kind": "text", "text": "Shipped."}
    assert parts["summary"] == {"kind": "data", "data": {"ok": 1}}
    assert parts["deck"]["kind"] == "file"
    assert parts["deck"]["file"]["name"] == "deck.pdf"
    assert base64.b64decode(parts["deck"]["file"]["bytes"]) == pdf
    assert parts["tampered"]["kind"] == "data"
    assert parts["tampered"]["data"]["content"].startswith("withheld")
    assert parts["large"]["kind"] == "data"
    assert parts["large"]["data"]["content"].startswith("not in the task")
    assert {a["metadata"]["choregos"]["digest"] for a in task["artifacts"]} >= {artifact_digest(pdf)}

    # The task's own budget: once the deck is in, the rest is read in the console.
    monkeypatch.setattr("choregos_api.a2a.door.MAX_ARTIFACTS_INLINE", len(pdf))
    task = (await _rpc(client, token, "tasks/get", {"id": task_id})).json()["result"]
    parts = {a["name"]: a["parts"][0] for a in task["artifacts"]}
    assert parts["deck"]["kind"] == "file"
    assert "capped" in parts["notes"]["data"]["content"]


async def test_tasks_cancel_stops_the_work_item(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.temporal import get_temporal

    token = await _ready(client, project)
    task = await _send(client, token)
    answer = (await _rpc(client, token, "tasks/cancel", {"id": task["id"]})).json()
    assert "result" in answer, answer
    controls = [s[2] for s in get_temporal().signals if s[1] == "control"]  # type: ignore[attr-defined]
    assert [c["action"] for c in controls] == ["stop"]

    # ───────────────────────────── guards ─────────────────────────────

    unknown = (await _rpc(client, token, "agent/dance", {})).json()
    assert unknown["error"]["code"] == -32601


async def test_the_door_is_rate_limited(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.a2a import door
    from choregos_api.limiteur import Limiteur

    token = await _ready(client, project)
    monkeypatch.setattr(door, "LIMITEUR", Limiteur(1))
    assert (await _rpc(client, token, "tasks/get", {"id": "x"})).status_code == 200
    limited = await _rpc(client, token, "tasks/get", {"id": "x"})
    assert limited.status_code == 429, limited.text
    assert limited.headers["retry-after"]


async def test_a_reference_a2a_client_drives_the_door(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    """The platform's own outbound client (ADR 0062) speaks to the door as to any A2A agent."""
    from choregos_adapters.a2a import A2aClient, text_part

    token = await _ready(client, project)
    async with A2aClient(f"http://test{ENDPOINT}", token, transport=ASGITransport(app=app)) as remote:
        sent = await remote.send(remote.message([text_part("Add credit notes")]))
        read = await remote.get_task(sent.id)
    assert sent.state == read.state == "working"
    assert read.id == sent.id


async def test_a_reference_a2a_client_sends_a_file_a_step_reads(
    app: Any, client: AsyncClient, project: dict[str, Any], store: Any
) -> None:
    """The outbound client's own `file_part`, not a hand-built dict: what a caller sends, a step reads."""
    from choregos_adapters.a2a import A2aClient, file_part, text_part
    from choregos_api.db.models import ArtifactRecord, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from choregos_contracts import artifact_digest
    from sqlalchemy import select

    token = await _ready(client, project, modes=FILE_MODES)
    async with A2aClient(f"http://test{ENDPOINT}", token, transport=ASGITransport(app=app)) as remote:
        parts = [text_part("Review the contract"), file_part("contract.pdf", PDF, "application/pdf")]
        sent = await remote.send(remote.message(parts))
    assert sent.state == "working"
    async with session_scope() as session:
        rows = list((await session.execute(select(ArtifactRecord))).scalars())
        item = await session.get(WorkItem, sent.id)
        assert item is not None
        session.add(
            Run(
                id="run-read",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="review",
                status="running",
                stage_input={"transition": {"outputs": [], "inputs": ["contract.pdf"]}},
            )
        )
        key = item.tracker_key
    assert [(r.name, r.run_id, r.digest) for r in rows] == [
        ("contract.pdf", f"a2a:{sent.id}", artifact_digest(PDF))
    ]
    assert store.puts == 1
    headers = {
        "Authorization": "Bearer "
        + mint_run_token("run-read", project_slug="billing-api", work_item_key=key, ttl_minutes=5)
    }
    read = await client.get("/api/v1/internal/runs/run-read/inputs/contract.pdf", headers=headers)
    assert read.status_code == 200 and read.content == PDF


# ───────────────────────────── message/stream, tasks/resubscribe ─────────────────────────────


async def _stream(
    client: AsyncClient, token: str, method: str, params: dict[str, Any]
) -> list[dict[str, Any]]:
    """The JSON-RPC answers of one SSE answer, in order; a stream that does not end fails the test."""
    import json

    async def read() -> list[dict[str, Any]]:
        frames: list[dict[str, Any]] = []
        async with client.stream(
            "POST",
            ENDPOINT,
            json={"jsonrpc": "2.0", "id": 7, "method": method, "params": params},
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
        ) as answer:
            assert answer.status_code == 200
            if not answer.headers["content-type"].startswith("text/event-stream"):
                return [json.loads(await answer.aread())]
            async for line in answer.aiter_lines():
                if line.startswith("data:"):
                    frames.append(json.loads(line.removeprefix("data:").strip()))
        return frames

    return await asyncio.wait_for(read(), 10)


async def _when_followed(then: Any) -> None:
    """Runs `then` once a stream listens to the project, as the orchestrator's events would come."""
    from choregos_api.events import get_bus

    for _ in range(200):
        if get_bus().subscriber_count("project:billing-api"):
            break
        await asyncio.sleep(0.02)
    await then()


async def _touch(key: str) -> None:
    from choregos_api.events import get_bus
    from choregos_contracts import ChoregosEvent, EventType

    get_bus().publish(
        ChoregosEvent.emit(
            EventType.WORKITEM_STATE_CHANGED, source="/test", subject=key, project_slug="billing-api"
        )
    )


async def test_message_stream_follows_the_work_item_to_its_end(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_adapters.a2a import A2aClient, text_part
    from choregos_api.db.models import ArtifactRecord, HumanRequest, Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from sqlalchemy import select

    token = await _ready(client, project)
    card = (await client.get(CARD, headers={"Authorization": f"Bearer {token}"})).json()
    assert card["capabilities"]["streaming"] is True

    async def drive() -> None:
        async with session_scope() as session:
            item = (await session.execute(select(WorkItem))).scalar_one()
            session.add(
                HumanRequest(
                    work_item_id=item.id,
                    project_id=project["id"],
                    kind="approval",
                    payload={"question": "Merge?"},
                    requested_at=utcnow(),
                )
            )
            key = item.tracker_key
        await _touch(key)
        await _touch("SOMEONE-ELSE-1")
        await asyncio.sleep(0.5)
        async with session_scope() as session:
            item = (await session.execute(select(WorkItem))).scalar_one()
            (await session.execute(select(HumanRequest))).scalar_one().decided_at = utcnow()
            item.state = "deployed_prod"
            session.add(
                ArtifactRecord(
                    org_id=(await session.get(Project, project["id"])).org_id,  # type: ignore[union-attr]
                    work_item_id=item.id,
                    name="release-notes",
                    version=1,
                    type="markdown",
                    kind="inline",
                    media_type="text/markdown",
                    digest="sha256:" + "0" * 64,
                    size=3,
                    content="ok",
                    run_id="run-1",
                )
            )
        await _touch(key)

    async def follow() -> list[Any]:
        async with A2aClient(f"http://test{ENDPOINT}", token, transport=ASGITransport(app=app)) as remote:
            return [e async for e in remote.stream(remote.message([text_part("Add credit notes")]))]

    events, _ = await asyncio.wait_for(asyncio.gather(follow(), _when_followed(drive)), 10)
    assert [(e.kind, e.state, e.final) for e in events] == [
        ("task", "working", False),
        ("status", "input-required", False),
        ("artifact", None, False),
        ("status", "completed", True),
    ], "a status per change, none for another work item's event, the artefacts before the final one"
    assert events[2].artifact is not None and events[2].artifact.name == "release-notes"


async def test_resubscribe_to_a_finished_task_says_final_and_closes(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    token = await _ready(client, project)
    task = await _send(client, token)
    async with session_scope() as session:
        item = await session.get(WorkItem, task["id"])
        assert item is not None
        item.failure = {"reason": "boom"}
    frames = await _stream(client, token, "tasks/resubscribe", {"id": task["id"]})
    results = [f["result"] for f in frames]
    assert [r["kind"] for r in results] == ["task", "status-update"]
    assert results[1]["status"]["state"] == "failed" and results[1]["final"] is True
    unknown = await _stream(client, token, "tasks/resubscribe", {"id": "nope"})
    assert unknown[0]["error"]["code"] == -32001, "refused before the stream, as plain JSON-RPC"


async def test_a_revoked_token_ends_the_stream(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import ApiToken, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from sqlalchemy import select

    token = await _ready(client, project)
    task = await _send(client, token)

    async def revoke() -> None:
        async with session_scope() as session:
            (await session.execute(select(ApiToken))).scalar_one().expires_at = utcnow()
            item = await session.get(WorkItem, task["id"])
            assert item is not None
            item.state = "refining"
        await _touch(task["metadata"]["choregos"]["key"])

    frames, _ = await asyncio.gather(
        _stream(client, token, "tasks/resubscribe", {"id": task["id"]}), _when_followed(revoke)
    )
    assert frames[0]["result"]["status"]["state"] == "working"
    assert frames[-1]["error"]["code"] == -32001, "the stream reads its credential again, and stops"
    assert len(frames) == 2, "nothing of the new state reached a caller whose token is gone"


async def test_open_streams_are_capped_per_token(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.a2a import door

    token = await _ready(client, project)
    monkeypatch.setattr(door, "MAX_STREAMS", 0)
    refused = await _stream(client, token, "message/stream", _message("x"))
    assert refused[0]["error"]["code"] == -32051
    assert door._STREAMS == {}, "a refused stream holds no slot"
