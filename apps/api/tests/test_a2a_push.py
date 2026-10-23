"""Push notifications of the A2A door (ADR 0078 §5, S40-23d).

The receiver is a scripted transport behind the platform's egress guard. Each guard has its test:
the secret shown once, the egress list at registration, the signature and the event id, the cursor
that does not move on a failure, the token read again before each delivery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from .test_a2a_inbound import _ready, _rpc, _send

HOOK = "https://hooks.partner.example/a2a"


@dataclass
class Receiver:
    status: int = 200
    calls: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(self.status)


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    from choregos_api.services.remote_agents import set_a2a_transport

    receiver = Receiver()
    set_a2a_transport(httpx.MockTransport(receiver))
    yield receiver
    set_a2a_transport(None)


@pytest.fixture
def enforce(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_EGRESS_MODE", "enforce")
    reset_settings_cache()


async def _set(client: AsyncClient, token: str, task_id: str, **config: Any) -> dict[str, Any]:
    params = {"taskId": task_id, "pushNotificationConfig": {"url": HOOK, "token": "partner-check", **config}}
    return dict((await _rpc(client, token, "tasks/pushNotificationConfig/set", params)).json())


async def _happen(task_id: str, state: str | None = None) -> str:
    """An event of the work item, persisted; with `state`, the work item moved first."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.evenements import persist_event
    from choregos_contracts import EventType

    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, task_id)
        assert item is not None
        if state:
            item.state = state
        event = await persist_event(
            session,
            EventType.WORKITEM_STATE_CHANGED,
            project_id=item.project_id,
            work_item_id=item.id,
            subject=item.tracker_key,
        )
        await session.flush()
        return event.id


async def _config(task_id: str) -> Any:
    from choregos_api.db.models import A2aPushConfig
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        return (
            await session.execute(select(A2aPushConfig).where(A2aPushConfig.work_item_id == task_id))
        ).scalar_one()


def _signed_by(request: httpx.Request, secret: str) -> bool:
    stamp, signature = (part.split("=", 1)[1] for part in request.headers["x-choregos-signature"].split(","))
    expected = hmac.new(secret.encode(), stamp.encode() + b"." + request.content, hashlib.sha256)
    return hmac.compare_digest(signature, expected.hexdigest())


async def test_the_secret_is_shown_once(client: AsyncClient, project: dict[str, Any]) -> None:
    token = await _ready(client, project)
    task = await _send(client, token)
    created = await _set(client, token, task["id"], id="ops")
    assert created["result"]["pushNotificationConfig"] == {"id": "ops", "url": HOOK}, created
    assert len(created["result"]["signing"]["secret"]) >= 32
    params = {"id": task["id"], "pushNotificationConfigId": "ops"}
    read = (await _rpc(client, token, "tasks/pushNotificationConfig/get", params)).json()
    listed = (await _rpc(client, token, "tasks/pushNotificationConfig/list", {"id": task["id"]})).json()
    assert read["result"]["pushNotificationConfig"]["url"] == HOOK
    assert [c["pushNotificationConfig"]["id"] for c in listed["result"]] == ["ops"]
    assert created["result"]["signing"]["secret"] not in json.dumps([read, listed])
    deleted = (await _rpc(client, token, "tasks/pushNotificationConfig/delete", params)).json()
    assert deleted["result"] is None, deleted
    gone = (await _rpc(client, token, "tasks/pushNotificationConfig/get", params)).json()
    assert gone["error"]["code"] == -32001


async def test_a_registration_needs_an_https_url_and_no_inline_config(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    token = await _ready(client, project)
    task = await _send(client, token)
    plain = await _set(client, token, task["id"], url="http://hooks.partner.example/a2a")
    assert plain["error"]["code"] == -32602, plain
    inline = (
        await _rpc(
            client,
            token,
            "message/send",
            {
                "message": {
                    "kind": "message",
                    "role": "user",
                    "messageId": "m-9",
                    "parts": [{"kind": "text", "text": "x"}],
                },
                "configuration": {"pushNotificationConfig": {"url": HOOK}},
            },
        )
    ).json()
    assert inline["error"]["code"] == -32602, inline


async def test_a_host_the_organisation_has_not_opened_is_refused_at_registration(
    client: AsyncClient, project: dict[str, Any], enforce: None
) -> None:
    from choregos_api.db.models import A2aPushConfig, Organization, OrgEgressRule
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    token = await _ready(client, project)
    task = await _send(client, token)
    refused = await _set(client, token, task["id"])
    assert refused["error"]["code"] == -32602, refused
    assert "egress" in refused["error"]["message"]
    async with session_scope(orgs="*") as session:
        assert (await session.execute(select(func.count()).select_from(A2aPushConfig))).scalar_one() == 0
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        session.add(OrgEgressRule(org_id=org.id, host="hooks.partner.example", status="allowed"))
    assert "result" in await _set(client, token, task["id"])


async def test_a_notification_is_signed_carries_the_event_id_and_ends_with_the_task(
    client: AsyncClient, project: dict[str, Any], receiver: Receiver
) -> None:
    from choregos_api.a2a.push import deliver_pending

    token = await _ready(client, project)
    task = await _send(client, token)
    secret = (await _set(client, token, task["id"]))["result"]["signing"]["secret"]
    first = await _happen(task["id"])
    assert len(await deliver_pending()) == 1
    (call,) = receiver.calls
    assert call.url == HOOK
    assert call.headers["x-a2a-notification-token"] == "partner-check"
    assert call.headers["x-choregos-event-id"] == call.headers["idempotency-key"] == first
    assert _signed_by(call, secret)
    assert not _signed_by(call, "another-secret")
    assert json.loads(call.content)["id"] == task["id"]

    assert await deliver_pending() == []  # nothing new
    await _happen(task["id"])  # an event that changes nothing the task says
    assert await deliver_pending() == []
    assert len(receiver.calls) == 1

    done = await _happen(task["id"], state="deployed_prod")
    assert len(await deliver_pending()) == 1
    assert receiver.calls[-1].headers["x-choregos-event-id"] == done
    assert json.loads(receiver.calls[-1].content)["status"]["state"] == "completed"
    await _happen(task["id"])
    assert await deliver_pending() == []  # the final state was delivered: the registration ended
    assert (await _config(task["id"])).finished_at is not None


async def test_a_failed_call_is_sent_again_with_the_same_event_id(
    client: AsyncClient, project: dict[str, Any], receiver: Receiver
) -> None:
    from choregos_api.a2a.push import deliver_pending
    from choregos_api.db.models import A2aPushConfig
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    token = await _ready(client, project)
    task = await _send(client, token)
    await _set(client, token, task["id"])
    event = await _happen(task["id"])
    receiver.status = 503
    assert await deliver_pending() == []
    config = await _config(task["id"])
    assert config.attempts == 1 and config.cursor_event_id is None and "503" in config.last_error
    assert await deliver_pending() == []  # not before its delay
    async with session_scope(orgs="*") as session:
        await session.execute(update(A2aPushConfig).values(next_attempt_at=None))
    receiver.status = 200
    assert len(await deliver_pending()) == 1
    assert [c.headers["x-choregos-event-id"] for c in receiver.calls] == [event, event]
    assert (await _config(task["id"])).cursor_event_id == event


async def test_a_revoked_token_stops_the_notifications(
    client: AsyncClient, project: dict[str, Any], receiver: Receiver
) -> None:
    from choregos_api.a2a.push import deliver_pending
    from choregos_api.db.models import ApiToken
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from sqlalchemy import update

    token = await _ready(client, project)
    task = await _send(client, token)
    await _set(client, token, task["id"])
    async with session_scope(orgs="*") as session:
        await session.execute(update(ApiToken).values(expires_at=utcnow()))
    await _happen(task["id"])
    assert await deliver_pending() == []
    assert receiver.calls == []
    config = await _config(task["id"])
    assert config.finished_at is not None and "expired" in config.last_error
