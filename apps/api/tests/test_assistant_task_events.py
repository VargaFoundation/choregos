"""The `trigger` kind of an assistant's task: an event routed to a subscriber starts their task (S63-21).

Each guard has its test: an event names its subscriber by the manifest's JSON pointer and starts the task in
THAT person's personal space only; the same event id never starts twice (the replay, the sender's key, the
digest); an event that names nobody, several people, nobody subscribed, or two accounts is refused, journaled
and starts nothing; the door takes only the task's signature; the subscriber's gate (delegation, budget,
audience) judges an event as it judges a window; a disabled subscription is nobody; the scheduler never runs
the kind; the manager reads the journal and the key, a member does not.
"""

# ruff: noqa: F811 - a fixture imported from another module is redefined by the parameter that names it

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .kits.assistant_install import ORG
from .test_assistant_tasks import (  # noqa: F401
    TASKS,
    _installation_id,
    _pass,
    _ready,
    _runs,
    _subscriber,
    as_admin,
    at,
    markets,
    offline,
)

DOOR = "/api/v1/webhooks/assistants/varga/investment-assistant/tasks"
INSTALLATION = f"{ORG}/assistants/investment-assistant"


async def _key(task: str = "meeting_prep") -> str:
    from choregos_api.services.task_events import event_key

    return event_key(await _installation_id(), 0, task)


async def _post(
    client: AsyncClient,
    payload: Any,
    *,
    task: str = "meeting_prep",
    key: str | None = None,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
) -> Any:
    body = raw if raw is not None else json.dumps(payload).encode()
    secret = key if key is not None else await _key(task)
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return await client.post(
        f"{DOOR}/{task}/events",
        content=body,
        headers={"X-Choregos-Signature": signature, "Content-Type": "application/json", **(headers or {})},
    )


def _anon(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _event(email: str, event_id: str = "ev-1") -> dict[str, Any]:
    return {"attendee": {"email": email}, "event": {"id": event_id}, "title": "Board"}


async def _journal() -> list[Any]:
    from choregos_api.db.models import TaskEvent
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(select(TaskEvent).order_by(TaskEvent.received_at, TaskEvent.id))
        ).scalars()
        found = list(rows)
        for row in found:
            session.expunge(row)
        return found


async def _subscribe(person: AsyncClient, task: str = "meeting_prep") -> None:
    put = await person.put(f"{TASKS}/{task}", json={"kind": "trigger"})
    assert put.status_code == 200, put.text
    assert put.json()["trigger"] == {"kind": "trigger", "cron": None, "timezone": "UTC"}


async def test_an_event_starts_the_task_of_the_person_it_names_once(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any
) -> None:
    await _ready(client)
    ann = await _subscriber(client, app, "ann@varga.dev")
    bob = await _subscriber(client, app, "bob@varga.dev")
    await _subscribe(ann)
    await _subscribe(bob)
    anonymous = _anon(app)

    sent = await _post(anonymous, _event("Ann@Varga.dev"))
    assert sent.status_code == 202, sent.text
    assert sent.json()["outcome"] == "started"
    (run,) = await _runs()
    item, owner = run
    assert owner == "ann@varga.dev", "only the named person's personal space"
    assert item.initiated_by == "person:ann@varga.dev"
    assert item.documents["task"]["task"] == "meeting_prep"
    assert item.documents["task"]["trigger"] == "trigger" and item.documents["task"]["object"] == "ev-1"
    assert item.tracker_key == sent.json()["work_item_key"]

    # The same event id again, even with another body: one run.
    again = await _post(anonymous, {**_event("ann@varga.dev"), "title": "changed"})
    assert again.status_code == 202 and again.json()["outcome"] == "duplicate"
    assert len(await _runs()) == 1
    # Another event, for the other person.
    other = await _post(anonymous, _event("bob@varga.dev", "ev-2"))
    assert other.json()["outcome"] == "started"
    assert sorted(o for _, o in await _runs()) == ["ann@varga.dev", "bob@varga.dev"]
    assert [e.outcome for e in await _journal()] == ["started", "duplicate", "started"]
    # The scheduler does not run the kind: no window, no run.
    assert await _pass(at(9, 0)) == {}
    assert len(await _runs()) == 2


async def test_the_event_id_is_the_senders_key_or_the_bodys_digest_when_the_trigger_names_no_pointer(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any
) -> None:
    await _ready(client)
    cat = await _subscriber(client, app, "cat@varga.dev")
    # `meeting_prep` reads `/event/id`; a payload without it falls back to the header, then the digest.
    await _subscribe(cat)
    anonymous = _anon(app)
    body = {"attendee": {"email": "cat@varga.dev"}}
    first = await _post(anonymous, body, headers={"Idempotency-Key": "k-1"})
    second = await _post(anonymous, body, headers={"Idempotency-Key": "k-1"})
    third = await _post(anonymous, body, headers={"Idempotency-Key": "k-2"})
    assert [r.json()["outcome"] for r in (first, second, third)] == ["started", "duplicate", "started"]
    digest = await _post(anonymous, body)
    replay = await _post(anonymous, body)
    assert [digest.json()["outcome"], replay.json()["outcome"]] == ["started", "duplicate"]


async def test_an_event_that_names_nobody_or_several_or_nobody_subscribed_is_refused_and_journaled(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any
) -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope

    await _ready(client)
    ann = await _subscriber(client, app, "ann@varga.dev")
    bob = await _subscriber(client, app, "bob@varga.dev")
    dup = await _subscriber(client, app, "dup@varga.dev")
    twin = await _subscriber(client, app, "twin@varga.dev")
    off = await _subscriber(client, app, "off@varga.dev")
    for person in (ann, bob, dup, twin):
        await _subscribe(person)
    assert (
        await off.put(f"{TASKS}/meeting_prep", json={"kind": "trigger", "enabled": False})
    ).status_code == 200
    # Two accounts that differ by case only: the address cannot name one of them.
    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == "twin@varga.dev"))).scalar_one()
        user.email = "DUP@varga.dev"
    anonymous = _anon(app)

    refusals = {
        "no pointer": {"event": {"id": "a"}},
        "not text": {"event": {"id": "b"}, "attendee": {"email": 7}},
        "unmatched": _event("stranger@varga.dev", "c"),
        "disabled": _event("off@varga.dev", "d"),
        "ambiguous": _event("dup@varga.dev", "e"),
    }
    for name, payload in refusals.items():
        refused = await _post(anonymous, payload)
        assert refused.status_code == 422, (name, refused.text)
    # Two addresses in one string are not an address.
    assert (await _post(anonymous, _event("ann@varga.dev, bob@varga.dev", "f"))).status_code == 422
    # Several people: `delegated_event` reads `/who/*`, and this event names two of them.
    several = await _post(anonymous, {"who": ["ann@varga.dev", "bob@varga.dev"]}, task="delegated_event")
    assert several.status_code == 422 and "several people" in several.text
    assert (await _post(anonymous, None, raw=b"not json")).status_code == 422
    assert await _runs() == [], "a refused event starts nothing"
    lines = await _journal()
    assert [line.outcome for line in lines] == ["refused"] * 8
    assert all("@" not in line.summary for line in lines), "the journal never keeps an address"
    assert {line.event_id for line in lines} >= {"a", "b", "c", "d", "e", "f"}
    # The corrected event goes through: a refusal keeps no dedup.
    assert (await _post(anonymous, _event("ann@varga.dev", "c"))).json()["outcome"] == "started"


async def test_the_door_takes_only_the_signature_of_that_task(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any
) -> None:
    await _ready(client)
    ann = await _subscriber(client, app, "ann@varga.dev")
    await _subscribe(ann)
    anonymous = _anon(app)
    body = json.dumps(_event("ann@varga.dev")).encode()
    assert (await anonymous.post(f"{DOOR}/meeting_prep/events", content=body)).status_code == 401
    wrong = await _post(anonymous, _event("ann@varga.dev"), key="chtev_nope")
    assert wrong.status_code == 401
    # Another task's key opens no other task's door.
    other_task = await _post(anonymous, _event("ann@varga.dev"), key=await _key("delegated_event"))
    assert other_task.status_code == 401
    # A task without a `trigger` has no door; nor does an unknown one.
    assert (await _post(anonymous, {}, task="morning_brief", key="x")).status_code == 404
    assert (await _post(anonymous, {}, task="nothing_here", key="x")).status_code == 404
    assert await _runs() == [] and await _journal() == []


async def test_the_subscribers_gate_judges_an_event_as_it_judges_a_window(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any
) -> None:
    from choregos_api.db.models import CostLedger, Organization, User
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    await _ready(client)
    dee = await _subscriber(client, app, "dee@varga.dev")
    await _subscribe(dee, "delegated_event")
    await _subscribe(dee)
    anonymous = _anon(app)

    # Reads on the person's behalf: no standing delegation, no run, and the reason is on the subscription.
    refused = await _post(anonymous, {"who": ["dee@varga.dev"]}, task="delegated_event")
    assert refused.status_code == 409 and "standing delegation" in refused.text
    assert "standing delegation" in (await dee.get(f"{TASKS}/delegated_event")).json()["last_refusal"]
    assert await _runs() == []

    # The month of the subscriber is spent: the same refusal as for a schedule or a click.
    await as_admin(client)
    assert (
        await client.patch(
            f"{INSTALLATION}", json={"policy_overrides": {"budget": {"per_user_month_usd": 1}}}
        )
    ).status_code == 200
    installation = await _installation_id()
    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == "dee@varga.dev"))).scalar_one()
        organisation = (await session.execute(select(Organization))).scalar_one()
        session.add(
            CostLedger(
                org_id=organisation.id,
                installation_id=installation,
                user_id=user.id,
                cost_usd=1.5,
                kind="model",
                ts=utcnow(),
            )
        )
    over = await _post(anonymous, _event("dee@varga.dev"))
    assert over.status_code == 409 and "monthly budget" in over.text
    assert await _runs() == []
    lines = await _journal()
    assert [line.outcome for line in lines] == ["refused", "refused"]
    assert lines[1].user_id == user.id, "a refusal at the gate knows its subscriber"


async def test_the_manager_reads_the_key_and_the_journal_a_member_does_not(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any
) -> None:
    await _ready(client)
    ann = await _subscriber(client, app, "ann@varga.dev")
    await _subscribe(ann)
    key_route = f"{INSTALLATION}/tasks/meeting_prep/event-key"
    assert (await ann.post(key_route)).status_code == 403
    assert (await ann.get(f"{INSTALLATION}/task-events")).status_code == 403
    await as_admin(client)
    shown = await client.post(key_route)
    assert shown.status_code == 200, shown.text
    assert shown.json()["secret"] == await _key() and shown.json()["generation"] == 0
    assert shown.json()["url"].endswith(
        "/api/v1/webhooks/assistants/varga/investment-assistant/tasks/meeting_prep/events"
    )
    assert (await client.post(f"{INSTALLATION}/tasks/morning_brief/event-key")).status_code == 404
    # Rotating the installation's webhook key changes the door's key and the old one stops opening it.
    old = shown.json()["secret"]
    assert (await client.post(f"{INSTALLATION}/webhook-secret", json={"rotate": True})).status_code == 200
    anonymous = _anon(app)
    assert (await _post(anonymous, _event("ann@varga.dev"), key=old)).status_code == 401
    new = (await client.post(key_route)).json()
    assert new["generation"] == 1 and new["secret"] != old
    assert (await _post(anonymous, _event("ann@varga.dev"), key=new["secret"])).status_code == 202
    await _post(anonymous, _event("nobody@varga.dev", "z"), key=new["secret"])
    listed = (await client.get(f"{INSTALLATION}/task-events")).json()["items"]
    assert [(i["event_id"], i["outcome"]) for i in listed] == [("z", "refused"), ("ev-1", "started")]


async def test_a_subscription_to_a_trigger_has_no_cron_and_the_kind_must_be_declared(
    client: AsyncClient, org: str, app: Any, offline: Path, markets: Any
) -> None:
    await _ready(client)
    ann = await _subscriber(client, app, "ann@varga.dev")
    assert (
        await ann.put(f"{TASKS}/meeting_prep", json={"kind": "trigger", "cron": "0 8 * * *"})
    ).status_code == 422
    assert (await ann.put(f"{TASKS}/meeting_prep", json={"kind": "schedule"})).status_code == 422
    assert (await ann.put(f"{TASKS}/morning_brief", json={"kind": "trigger"})).status_code == 422
