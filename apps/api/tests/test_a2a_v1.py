"""The A2A door in A2A 1.0 (S62-02): the same calls as 0.3.0, read and answered in 1.0's shapes.

`v1.py` only translates: each test sends a 1.0 call and checks the answer's shape, and that the guard
behind it is the one 0.3.0 has (the same task for the same `messageId`, the same refusals).
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import yaml
from httpx import ASGITransport, AsyncClient, Response

from .test_a2a_inbound import CARD, ENDPOINT, _ready, _rpc, _send, _stream, _touch, _when_followed
from .test_a2a_push import HOOK, Receiver, _happen


@pytest.fixture
def hook() -> Iterator[Receiver]:
    """The receiver of a push, behind the platform's egress guard (as `test_a2a_push.receiver`)."""
    from choregos_api.services.remote_agents import set_a2a_transport

    receiver = Receiver()
    set_a2a_transport(httpx.MockTransport(receiver))
    yield receiver
    set_a2a_transport(None)


async def _call(
    client: AsyncClient, token: str, method: str, params: dict[str, Any], version: str | None = None
) -> Response:
    headers = {"Authorization": f"Bearer {token}"}
    if version:
        headers["A2A-Version"] = version
    return await client.post(
        ENDPOINT, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, headers=headers
    )


def _message(*parts: dict[str, Any], message_id: str = "m-1") -> dict[str, Any]:
    return {
        "message": {"role": "ROLE_USER", "messageId": message_id, "parts": list(parts) or [{"text": "Add"}]}
    }


async def _send_v1(client: AsyncClient, token: str, *parts: dict[str, Any], message_id: str = "m-1") -> Any:
    return (await _call(client, token, "SendMessage", _message(*parts, message_id=message_id))).json()


def _info(answer: dict[str, Any]) -> dict[str, Any]:
    details = answer["error"]["data"]
    assert isinstance(details, list) and details, answer
    assert details[0]["@type"] == "type.googleapis.com/google.rpc.ErrorInfo", details
    return dict(details[0])


# ───────────────────────────── the card ─────────────────────────────


async def test_the_card_offers_1_0_first_at_the_same_url(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_adapters.a2a import parse_card

    token = await _ready(client, project)
    raw = (await client.get(CARD, headers={"Authorization": f"Bearer {token}"})).json()
    interfaces = raw["supportedInterfaces"]
    assert [(i["protocolVersion"], i["protocolBinding"]) for i in interfaces] == [
        ("1.0", "JSONRPC"),
        ("0.3", "JSONRPC"),
    ]
    assert {i["url"] for i in interfaces} == {raw["url"]}
    assert raw["securitySchemes"]["bearer"]["httpAuthSecurityScheme"] == {"scheme": "Bearer"}
    assert {"schemes": {"bearer": {"list": []}}} in raw["securityRequirements"]
    assert parse_card(raw).protocol_version == "0.3.0", "a 0.3.0 client still reads the same card"


# ───────────────────────────── SendMessage, GetTask, CancelTask ─────────────────────────────


async def test_send_message_is_message_send(client: AsyncClient, project: dict[str, Any]) -> None:
    token = await _ready(client, project)
    answer = await _send_v1(client, token, message_id="m-42")
    task = answer["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_WORKING", answer
    message = task["status"]["message"]
    assert message["role"] == "ROLE_AGENT"
    assert all("kind" not in p for p in message["parts"]), "a 1.0 part says what it is by its field"
    assert task["metadata"]["choregos"]["project"] == "varga:billing-api"
    same = await _send(client, token, message_id="m-42")
    assert same["id"] == task["id"], "the same messageId is the same task in either version"
    read = (await _call(client, token, "GetTask", {"id": task["id"]})).json()["result"]
    assert read["id"] == task["id"] and read["status"]["state"] == "TASK_STATE_WORKING"


async def test_a_reference_1_0_client_drives_the_door(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_adapters.a2a import A2aClient, text_part

    token = await _ready(client, project)
    async with A2aClient(f"http://test{ENDPOINT}", token, "1.0", transport=ASGITransport(app=app)) as remote:
        sent = await remote.send(remote.message([text_part("Add credit notes")]))
        read = await remote.get_task(sent.id)
    assert sent.state == read.state == "working"
    assert read.id == sent.id


async def test_cancel_task_stops_the_work_item(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.temporal import get_temporal

    token = await _ready(client, project)
    task = (await _send_v1(client, token))["result"]["task"]
    answer = (await _call(client, token, "CancelTask", {"id": task["id"]})).json()
    assert answer["result"]["id"] == task["id"], answer
    assert answer["result"]["status"]["state"] == "TASK_STATE_CANCELED", answer
    controls = [s[2] for s in get_temporal().signals if s[1] == "control"]  # type: ignore[attr-defined]
    assert [c["action"] for c in controls] == ["stop"]


async def test_a_file_is_sent_in_1_0_parts(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import ArtifactRecord
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    token = await _ready(client, project, modes=["text/plain", "text/markdown"])
    raw = base64.b64encode(b"# Spec\n").decode()
    sent = await _send_v1(
        client, token, {"text": "Add"}, {"raw": raw, "mediaType": "text/markdown", "filename": "spec.md"}
    )
    assert "result" in sent, sent
    async with session_scope(orgs="*") as session:
        names = [a.name for a in (await session.execute(select(ArtifactRecord))).scalars()]
    assert names == ["spec.md"]

    by_url = await _send_v1(
        client, token, {"url": "https://x.example/a.md", "mediaType": "text/markdown"}, message_id="m-2"
    )
    assert by_url["error"]["code"] == -32602
    assert "`url` is not fetched: send its content in `raw`" in by_url["error"]["message"]
    old_shape = {"kind": "file", "file": {"bytes": raw, "mimeType": "text/markdown", "name": "a.md"}}
    mixed = await _send_v1(client, token, old_shape, message_id="m-3")
    assert mixed["error"]["code"] == -32602, "a 0.3.0 part in a 1.0 call is not guessed at"
    assert "`raw`" in mixed["error"]["message"]
    unknown_type = await _send_v1(
        client, token, {"raw": raw, "mediaType": "application/pdf", "filename": "a.pdf"}, message_id="m-4"
    )
    assert unknown_type["error"]["code"] == -32005
    assert _info(unknown_type)["reason"] == "CONTENT_TYPE_NOT_SUPPORTED"


# ───────────────────────────── errors and versions ─────────────────────────────


async def test_a_refusal_carries_an_error_info(client: AsyncClient, project: dict[str, Any]) -> None:
    token = await _ready(client, project)
    missing = (await _call(client, token, "GetTask", {"id": "nope"})).json()
    assert missing["error"]["code"] == -32001
    assert _info(missing) == {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "TASK_NOT_FOUND",
        "domain": "a2a-protocol.org",
    }
    old = (await _rpc(client, token, "tasks/get", {"id": "nope"})).json()
    assert "data" not in old["error"] or not isinstance(old["error"]["data"], list), "0.3.0 keeps its shape"


async def test_fields_a_workflow_refuses_are_field_violations(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_core.dsl import template_yaml

    await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    workflow = yaml.safe_load(template_yaml("default-simple"))
    workflow["metadata"]["inputs"] = {
        "type": "object",
        "required": ["ticket"],
        "properties": {"ticket": {"type": "string"}},
    }
    workflow["a2a"] = {
        "skill": "ship-a-change",
        "description": "Ship a change.",
        "input_modes": ["text/plain", "application/json"],
    }
    published = await client.put(
        f"/api/v1/projects/{project['id']}/workflow", json={"yaml": yaml.safe_dump(workflow, sort_keys=False)}
    )
    assert published.status_code == 200, published.text
    token = (await client.post("/api/v1/me/tokens", json={"name": "p", "scopes": ["a2a"]})).json()["token"]
    client.cookies.clear()
    refused = await _send_v1(client, token, {"text": "Add"}, {"data": {"priority": "high"}})
    assert refused["error"]["code"] == -32602, refused
    details = refused["error"]["data"]
    assert details[0]["reason"] == "INVALID_PARAMS"
    violations = details[1]
    assert violations["@type"] == "type.googleapis.com/google.rpc.BadRequest"
    assert any("ticket" in v["description"] or v["field"] == "ticket" for v in violations["fieldViolations"])


async def test_a_version_the_door_does_not_speak_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    token = await _ready(client, project)
    future = (await _call(client, token, "SendMessage", _message(), version="2.0")).json()
    assert future["error"]["code"] == -32009, future
    assert _info(future)["metadata"] == {"supportedVersions": "0.3, 1.0"}
    crossed = (await _call(client, token, "message/send", _message(), version="1.0")).json()
    assert crossed["error"]["code"] == -32601, "a header that disagrees with the method is not guessed at"
    patch = await _send_v1(client, token)
    with_patch = (
        await _call(client, token, "GetTask", {"id": patch["result"]["task"]["id"]}, "1.0.3")
    ).json()
    assert "result" in with_patch, "a patch version reads as its Major.Minor"


async def test_the_1_0_methods_not_served_say_so(client: AsyncClient, project: dict[str, Any]) -> None:
    token = await _ready(client, project)
    listing = (await _call(client, token, "ListTasks", {})).json()
    assert listing["error"]["code"] == -32004
    assert _info(listing)["reason"] == "UNSUPPORTED_OPERATION"
    extended = (await _call(client, token, "GetExtendedAgentCard", {})).json()
    assert extended["error"]["code"] == -32007
    assert _info(extended)["reason"] == "EXTENDED_AGENT_CARD_NOT_CONFIGURED"


async def test_the_daily_budget_is_the_door_s_own_domain(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    from choregos_api.a2a import door

    token = await _ready(client, project)
    monkeypatch.setattr(door, "WRITES_PER_DAY", 0)
    spent = await _send_v1(client, token)
    assert spent["error"]["code"] == -32050, spent
    assert _info(spent)["domain"] == "choregos.dev"


# ───────────────────────────── streams ─────────────────────────────


async def test_subscribe_to_a_finished_task_gives_the_task_and_closes(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    token = await _ready(client, project)
    task = (await _send_v1(client, token))["result"]["task"]
    async with session_scope() as session:
        item = await session.get(WorkItem, task["id"])
        assert item is not None
        item.failure = {"reason": "boom"}
    frames = await _stream(client, token, "SubscribeToTask", {"id": task["id"]})
    results = [f["result"] for f in frames]
    assert [list(r) for r in results] == [["task"]], "1.0 has no `final`: the stream's end is the task's"
    assert results[0]["task"]["status"]["state"] == "TASK_STATE_FAILED"


async def test_send_streaming_message_follows_the_work_item_to_its_end(
    app: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_adapters.a2a import A2aClient, text_part
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    token = await _ready(client, project)

    async def drive() -> None:
        async with session_scope() as session:
            item = (await session.execute(select(WorkItem))).scalar_one()
            item.state = "deployed_prod"
            key = item.tracker_key
        await _touch(key)

    async def follow() -> list[Any]:
        async with A2aClient(
            f"http://test{ENDPOINT}", token, "1.0", transport=ASGITransport(app=app)
        ) as remote:
            return [e async for e in remote.stream(remote.message([text_part("Add credit notes")]))]

    events, _ = await asyncio.wait_for(asyncio.gather(follow(), _when_followed(drive)), 10)
    assert [(e.kind, e.state) for e in events] == [("task", "working"), ("status", "completed")]
    assert not any(e.final for e in events), "1.0 has no `final`: the stream closed after the last status"


# ───────────────────────────── push notifications ─────────────────────────────


async def test_push_configs_in_1_0_are_flat_and_notified_as_a_stream_response(
    client: AsyncClient, project: dict[str, Any], hook: Receiver
) -> None:
    from choregos_api.a2a.push import deliver_pending

    token = await _ready(client, project)
    task = (await _send_v1(client, token))["result"]["task"]
    params = {"taskId": task["id"], "id": "ops", "url": HOOK, "token": "partner-check"}
    created = (await _call(client, token, "CreateTaskPushNotificationConfig", params)).json()["result"]
    assert {k: created[k] for k in ("id", "taskId", "url")} == {
        "id": "ops",
        "taskId": task["id"],
        "url": HOOK,
    }
    assert len(created["signing"]["secret"]) >= 32, "the secret is still shown once, beside the config"
    which = {"taskId": task["id"], "id": "ops"}
    read = (await _call(client, token, "GetTaskPushNotificationConfig", which)).json()["result"]
    assert read["url"] == HOOK and "signing" not in read
    listed = (await _call(client, token, "ListTaskPushNotificationConfigs", {"taskId": task["id"]})).json()
    assert [c["id"] for c in listed["result"]["configs"]] == ["ops"]

    await _happen(task["id"], state="deployed_prod")
    assert len(await deliver_pending()) == 1
    body = json.loads(hook.calls[-1].content)
    assert list(body) == ["task"] and body["task"]["status"]["state"] == "TASK_STATE_COMPLETED"

    deleted = (await _call(client, token, "DeleteTaskPushNotificationConfig", which)).json()
    assert deleted["result"] == {}, deleted
    inline = await _call(
        client,
        token,
        "SendMessage",
        {**_message(message_id="m-p"), "configuration": {"taskPushNotificationConfig": {"url": HOOK}}},
    )
    assert "CreateTaskPushNotificationConfig" in inline.json()["error"]["message"]
