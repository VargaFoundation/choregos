"""An assistant's tasks: subscriptions, schedules per time zone, `poll`, delivery (S63-13, ADR 0137).

Each guard has its test: two subscribers of one task run at THEIR local hour, each window once (a simulated
clock); a poll starts one run per object and never the same object twice, even when its window is claimed
again; a run's artifact is in the subscriber's inbox and nobody else's, and the other channels send it once
(a notifier fake, an HMAC-signed webhook); a task that needs a delegation is subscribed but its scheduled
runs are refused with the reason, while its on-demand run starts; the subscription's own rules (an unknown
time zone, a cron nobody may change, an outsider's 404, unsubscribing).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from choregos_adapters.fakes.notify import FakeNotifier
from choregos_api.services.task_poll import condition_holds, due_objects, object_keys, resolve, when_holds
from httpx import AsyncClient
from sqlalchemy import select

from .kits.assistant_install import (
    ORG,
    _member,
)
from .kits.assistant_tasks import (  # noqa: F401
    AUDIENCE,
    ME,
    MONDAY,
    TASK_BLOCK,
    TASKS,
    WORKFLOW,
    _as,
    _installation_id,
    _pass,
    _produce,
    _ready,
    _runs,
    _subscriber,
    as_admin,
    at,
    markets,
    offline,
)
from .test_agent_sessions import Upstream, upstream  # noqa: F401


def _keys(runs: list[Any]) -> list[str]:
    return [item.tracker_key for item, _ in runs]


# ───────────────────────── a schedule per time zone ─────────────────────────


async def test_each_subscriber_runs_at_their_own_local_hour_once(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    await _ready(client)
    paris = await _subscriber(client, app, "paris@varga.dev")
    bogota = await _subscriber(client, app, "bogota@varga.dev")
    for person, zone in ((paris, "Europe/Paris"), (bogota, "America/Bogota")):
        put = await person.put(f"{TASKS}/morning_brief", json={"kind": "schedule", "timezone": zone})
        assert put.status_code == 200, put.text
        assert put.json()["trigger"] == {"kind": "schedule", "cron": "30 7 * * 1-5", "timezone": zone}

    assert await _pass(at(6, 0)) == {}, "07:30 has not come anywhere"
    # 07:30 in Paris (UTC+1 in winter) is 06:30 UTC: Bogota (UTC-5) is still asleep.
    first = await _pass(at(6, 30))
    assert list(first.values()) == ["schedule:20300107T0630Z"]
    assert [o for _, o in await _runs()] == ["paris@varga.dev"]
    # The same window again, and a minute later: nothing more.
    assert await _pass(at(6, 30)) == {} and await _pass(at(6, 31)) == {}
    assert await _pass(at(12, 0)) == {}
    # 07:30 in Bogota is 12:30 UTC.
    second = await _pass(at(12, 30))
    assert list(second.values()) == ["schedule:20300107T1230Z"]
    runs = await _runs()
    assert sorted(o for _, o in runs) == ["bogota@varga.dev", "paris@varga.dev"]
    paris_run = (await _runs("paris@varga.dev"))[0][0]
    assert paris_run.initiated_by == "person:paris@varga.dev"
    assert paris_run.documents["task"]["task"] == "morning_brief"
    assert paris_run.documents["labels"] == ["task:investment-assistant:morning_brief"]
    assert paris_run.state == "brief" and paris_run.tracker_key.endswith("20300107T0630Z")
    assert len(_keys(runs)) == 2, "one run per subscriber and window"


async def test_a_window_that_passed_before_subscribing_does_not_run(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.schemas.tasks import TaskSubscriptionPut
    from choregos_api.services.assistant_tasks import put_subscription

    await _ready(client)
    await _subscriber(client, app, "late@varga.dev")
    from choregos_api.db.models import User
    from choregos_api.rbac import Principal, Role

    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == "late@varga.dev"))).scalar_one()
        principal = Principal(
            user_id=user.id, email=user.email, org_roles={"varga": Role.VIEWER}, groups=[AUDIENCE]
        )
        await put_subscription(
            session,
            principal,
            "varga",
            "investment-assistant",
            "morning_brief",
            TaskSubscriptionPut(kind="schedule", timezone="Europe/Paris"),
            now=at(6, 40),
        )
    assert await _pass(at(6, 45)) == {}, "07:30 Paris passed (06:30Z) before the subscription"
    # A positive control: the next day's window does run.
    tomorrow = datetime(2030, 1, 8, 6, 30, tzinfo=UTC)
    assert list((await _pass(tomorrow)).values()) == ["schedule:20300108T0630Z"]


# ───────────────────────── poll ─────────────────────────


async def test_a_poll_starts_one_run_per_object_and_never_the_same_object_twice(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    from choregos_api.db.models import TaskSubscription
    from choregos_api.db.session import session_scope

    await _ready(client)
    person = await _subscriber(client, app, "poller@varga.dev")
    put = await person.put(f"{TASKS}/threshold_alert", json={"kind": "poll"})
    assert put.status_code == 200, put.text

    markets.structured["get_quote"] = {
        "events": [{"id": "e1", "change_pct": 4.2}, {"id": "e2", "change_pct": 1.0}]
    }
    started = await _pass(at(9, 0))
    assert list(started.values()) == ["poll:e1"], "e2 does not meet `when`"
    assert [i.documents["task"]["object"] for i, _ in await _runs()] == ["e1"]

    # The next window: e1 is still there (not started again), e3 is new, e2 now meets the condition.
    markets.structured["get_quote"] = {
        "events": [
            {"id": "e1", "change_pct": 4.2},
            {"id": "e2", "change_pct": -7},
            {"id": "e3", "change_pct": 3},
        ]
    }
    assert list((await _pass(at(9, 15))).values()) == ["poll:e2,e3"]
    # A second replica takes the same window again (the claim is lost): no object starts twice.
    async with session_scope(orgs="*") as session:
        sub = (await session.execute(select(TaskSubscription))).scalar_one()
        assert sorted(sub.seen_keys) == ["e1", "e2", "e3"]
        sub.last_window = None
    assert await _pass(at(9, 15)) == {}
    assert sorted(i.documents["task"]["object"] for i, _ in await _runs()) == ["e1", "e2", "e3"]
    # A result that does not meet `when` starts nothing.
    markets.structured["get_quote"] = {"events": [{"id": "e9", "change_pct": 0.1}]}
    assert await _pass(at(9, 30)) == {}


async def test_a_poll_whose_read_fails_starts_nothing_and_says_why(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    await _ready(client)
    person = await _subscriber(client, app, "poller@varga.dev")
    await person.put(f"{TASKS}/threshold_alert", json={"kind": "poll"})
    markets.structured["get_quote"] = {"events": [{"id": "e1", "change_pct": 9}]}
    from choregos_api.db.models import ConnectorOperation
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for row in (
            await session.execute(select(ConnectorOperation).where(ConnectorOperation.name == "get_quote"))
        ).scalars():
            row.policy = "forbidden"
    assert await _pass(at(9, 0)) == {}
    assert await _runs() == []
    got = await person.get(f"{TASKS}/threshold_alert")
    assert "get_quote" in got.json()["last_refusal"]


def test_the_pointers_and_conditions() -> None:
    result = {"events": [{"id": "a", "pct": -4}, {"id": "b", "pct": 1}], "tags": []}
    assert resolve(result, "/events/*/id") == ["a", "b"] and resolve(result, "/events/5") == []
    assert resolve({"a/b": {"c~d": 1}}, "/a~1b/c~0d") == [1]
    assert condition_holds({"abs_gte": 3}, [-4, 1]) and not condition_holds({"abs_gte": 5}, [-4, 1])
    assert (
        condition_holds({"gte": 1}, [1])
        and condition_holds({"lte": 0}, [-1])
        and condition_holds({"eq": "x"}, ["x"])
    )
    assert condition_holds("not_empty", resolve(result, "/events")) and not condition_holds("not_empty", [[]])
    assert not condition_holds({"gte": 1}, ["n/a"]) and not condition_holds({"nonsense": 1}, [1])
    assert when_holds({"/events": "not_empty", "/events/*/pct": {"abs_gte": 4}}, result)
    assert not when_holds({"/events": "not_empty", "/tags": "not_empty"}, result) and not when_holds(
        {}, result
    )
    assert due_objects({"/events/*/pct": {"abs_gte": 3}}, "/events/*/id", result) == ["a"]
    assert object_keys(None, {"x": 1}) == object_keys(None, {"x": 1}) != object_keys(None, {"x": 2})


# ───────────────────────── delegation (S63-14) ─────────────────────────


async def test_a_delegated_task_is_subscribed_but_its_scheduled_run_is_refused(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    await _ready(client)
    person = await _subscriber(client, app, "delegate@varga.dev")
    put = await person.put(f"{TASKS}/delegated_brief", json={"kind": "schedule", "timezone": "Europe/Paris"})
    assert put.status_code == 200, put.text
    assert put.json()["delegation"] == "read_only" and put.json()["delegation_id"] is None

    assert await _pass(at(6, 30)) == {}
    assert await _runs() == [], "no run reads on a person's behalf without a standing delegation"
    refused = (await person.get(f"{TASKS}/delegated_brief")).json()
    assert "standing delegation" in refused["last_refusal"]
    # On demand, the session's identity: no delegation is needed.
    ran = await person.post(f"{TASKS}/delegated_brief/run")
    assert ran.status_code == 201, ran.text
    assert len(await _runs()) == 1


# ───────────────────────── inbox and delivery ─────────────────────────


async def test_the_artifact_is_in_the_subscribers_inbox_and_nobody_elses(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    await _ready(client)
    mine = await _subscriber(client, app, "mine@varga.dev")
    other = await _subscriber(client, app, "other@varga.dev")
    assert (await mine.get(f"{ME}/inbox")).json() == {"items": []}
    ran = await mine.post(f"{TASKS}/morning_brief/run")
    assert ran.status_code == 201, ran.text
    run = ran.json()
    assert run["key"].startswith("TASK-morning_brief-") and run["project"].startswith("personal-")
    await _produce(run["work_item_id"], brief="# Your morning brief\n\nNothing moved.")

    items = (await mine.get(f"{ME}/inbox")).json()["items"]
    assert [(i["task"], i["installation"], i["name"], i["content"]) for i in items] == [
        ("morning_brief", "investment-assistant", "brief", "# Your morning brief\n\nNothing moved.")
    ]
    assert (await other.get(f"{ME}/inbox")).json() == {"items": []}
    assert (await other.post(f"{TASKS}/morning_brief/run")).status_code == 201
    assert len((await mine.get(f"{ME}/inbox")).json()["items"]) == 1, "another person's run is not mine"


async def test_the_other_channels_send_each_artifact_once(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.remote_agents import set_a2a_transport
    from choregos_api.services.task_delivery import deliver_pending, set_notifier_factory, webhook_secret

    await _ready(client)
    mine = await _subscriber(client, app, "mine@varga.dev")
    await as_admin(client)
    patched = await client.patch(
        f"{ORG}/assistants/investment-assistant",
        json={"policy_overrides": {"delivery": {"webhook": {"url": "https://apps.test/hook"}}}},
    )
    assert patched.status_code == 200, patched.text
    run = (await mine.post(f"{TASKS}/morning_brief/run")).json()
    await _produce(run["work_item_id"], brief="Hello")

    notifier = FakeNotifier()
    received: list[httpx.Request] = []

    def receiver(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    set_notifier_factory(lambda row, channel: notifier if channel == "email" else None)
    set_a2a_transport(httpx.MockTransport(receiver))
    try:
        for _ in range(2):
            async with session_scope(orgs="*") as session:
                await deliver_pending(session)
    finally:
        set_notifier_factory(None)
        set_a2a_transport(None)

    assert [(channel, message.title) for channel, message in notifier.sent] == [
        ("mine@varga.dev", "morning_brief: brief")
    ]
    assert notifier.sent[0][1].body == "Hello"
    assert len(received) == 1, "the webhook got the artifact once"
    request = received[0]
    body = request.content
    stamp, signature = re.fullmatch(
        r"t=(\d+),v1=([0-9a-f]{64})", request.headers["x-choregos-signature"]
    ).groups()  # type: ignore[union-attr]
    secret = webhook_secret(await _installation_id())
    assert hmac.compare_digest(
        signature, hmac.new(secret.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )
    sent = json.loads(body)
    assert sent["artifact"]["name"] == "brief" and sent["artifact"]["content"] == "Hello"
    assert sent["task"] == "morning_brief" and sent["subscriber"] == "mine@varga.dev"
    assert request.headers["idempotency-key"] == sent["artifact"]["id"]


async def test_a_channel_nobody_configured_is_recorded_not_silent(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.task_delivery import deliver_pending

    await _ready(client)
    mine = await _subscriber(client, app, "mine@varga.dev")
    run = (await mine.post(f"{TASKS}/morning_brief/run")).json()
    await _produce(run["work_item_id"], brief="Hello")
    async with session_scope(orgs="*") as session:
        assert await deliver_pending(session) == 0
    ((item, _),) = await _runs()
    ledger = item.documents["task_delivery"]["brief@1"]
    assert ledger["email"]["state"] == "not_configured" and ledger["webhook"]["state"] == "not_configured"
    assert (await mine.get(f"{ME}/inbox")).json()["items"][0]["name"] == "brief", "the inbox needs no channel"


# ───────────────────────── the subscription itself ─────────────────────────


async def test_the_subscription_rules(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    await _ready(client)
    await _member(client, "outsider@varga.dev", ["somebody-else"])
    outsider = await _as(app, "outsider@varga.dev")
    person = await _subscriber(client, app, "mine@varga.dev")

    assert (await outsider.put(f"{TASKS}/morning_brief", json={})).status_code == 404
    assert (await person.get(f"{TASKS}/morning_brief")).status_code == 404, "not subscribed yet"
    assert (await person.put(f"{TASKS}/nope", json={})).status_code == 404
    bad_zone = await person.put(f"{TASKS}/morning_brief", json={"timezone": "Mars/Olympus"})
    assert bad_zone.status_code == 422 and "Mars/Olympus" in bad_zone.text
    assert (await person.put(f"{TASKS}/morning_brief", json={"kind": "poll"})).status_code == 422
    no_zone = await person.put(f"{TASKS}/threshold_alert", json={"kind": "poll", "timezone": "Europe/Paris"})
    assert no_zone.status_code == 422, "a poll is not read in the person's time zone"
    assert (
        await person.put(f"{TASKS}/threshold_alert", json={"kind": "poll", "cron": "* * * * *"})
    ).status_code == 422

    own_hour = await person.put(
        f"{TASKS}/morning_brief", json={"kind": "schedule", "cron": "0 6 * * *", "timezone": "America/Bogota"}
    )
    assert own_hour.status_code == 200 and own_hour.json()["trigger"]["cron"] == "0 6 * * *"
    assert (await person.get(f"{TASKS}/morning_brief")).json()["trigger"]["timezone"] == "America/Bogota"
    off = await person.put(f"{TASKS}/morning_brief", json={"timezone": "America/Bogota", "enabled": False})
    assert off.json()["enabled"] is False
    assert await _pass(at(11, 30)) == {}, "a switched-off subscription never runs"

    # Nobody reads another person's subscription.
    other = await _subscriber(client, app, "other@varga.dev")
    assert (await other.get(f"{TASKS}/morning_brief")).status_code == 404

    assert (await person.delete(f"{TASKS}/morning_brief")).status_code == 204
    assert (await person.get(f"{TASKS}/morning_brief")).status_code == 404
    assert (await person.delete(f"{TASKS}/morning_brief")).status_code == 404


async def test_a_run_is_refused_when_the_subscribers_month_is_spent(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    from choregos_api.db.models import CostLedger, Organization, User
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    await _ready(client)
    mine = await _subscriber(client, app, "mine@varga.dev")
    await as_admin(client)
    assert (
        await client.patch(
            f"{ORG}/assistants/investment-assistant",
            json={"policy_overrides": {"budget": {"per_user_month_usd": 1}}},
        )
    ).status_code == 200
    installation = await _installation_id()
    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == "mine@varga.dev"))).scalar_one()
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
    refused = await mine.post(f"{TASKS}/morning_brief/run")
    assert refused.status_code == 409 and "monthly budget" in refused.text
    assert await _runs() == []


# ───────────────────────── spend, the webhook key, the `run_task` tool (E3) ─────────────────────────


async def test_the_spend_of_a_task_run_is_attributed_to_the_installation_and_stops_the_next_run(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    from choregos_api.db.models import CostLedger, Project, Run, User, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.assistant_access import month_spend
    from choregos_api.services.costs import record_cost

    await _ready(client)
    mine = await _subscriber(client, app, "mine@varga.dev")
    await as_admin(client)
    assert (
        await client.patch(
            f"{ORG}/assistants/investment-assistant",
            json={"policy_overrides": {"budget": {"per_user_month_usd": 1}}},
        )
    ).status_code == 200
    run = (await mine.post(f"{TASKS}/morning_brief/run")).json()
    installation = await _installation_id()
    async with session_scope(orgs="*") as session:
        item = await session.get(WorkItem, run["work_item_id"])
        assert item is not None
        project = await session.get(Project, item.project_id)
        assert project is not None
        session.add(
            Run(id="run-spend-1", work_item_id=item.id, project_id=item.project_id, stage_role="brief",
                status="succeeded")
        )  # fmt: skip
        await session.flush()
        await record_cost(
            session, project_id=project.id, work_item_id=item.id, run_id="run-spend-1", provider="p",
            model="m", backend=None, stage_role="brief", size=None, tokens_in=1, tokens_out=1,
            tokens_cached=0, cost_usd=1.5, fx_rate=1.0,
        )  # fmt: skip
    async with session_scope(orgs="*") as session:
        line = (
            await session.execute(select(CostLedger).where(CostLedger.run_id == "run-spend-1"))
        ).scalar_one()
        user = (await session.execute(select(User).where(User.email == "mine@varga.dev"))).scalar_one()
        assert (line.installation_id, line.user_id) == (installation, user.id)
        assert await month_spend(session, installation, user.id) == 1.5
        assert await month_spend(session, installation) == 1.5
    refused = await mine.post(f"{TASKS}/morning_brief/run")
    assert refused.status_code == 409 and "monthly budget" in refused.text
    other = await _subscriber(client, app, "other@varga.dev")
    assert (await other.post(f"{TASKS}/morning_brief/run")).status_code == 201, "another person's month"


async def test_the_webhook_key_is_shown_once_to_a_manager_and_rotates(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services.remote_agents import set_a2a_transport
    from choregos_api.services.task_delivery import deliver_pending, webhook_secret

    await _ready(client)
    mine = await _subscriber(client, app, "mine@varga.dev")
    route = f"{ORG}/assistants/investment-assistant/webhook-secret"
    assert (await mine.post(route)).status_code == 403, "a member of the audience does not manage"
    await as_admin(client)
    installation = await _installation_id()
    first = await client.post(route)
    assert first.status_code == 200, first.text
    assert first.json()["secret"] == webhook_secret(installation) and first.json()["generation"] == 0
    again = await client.post(route)
    assert again.status_code == 409 and again.json()["code"] == "already_shown"
    rotated = await client.post(route, json={"rotate": True})
    assert rotated.status_code == 200 and rotated.json()["generation"] == 1
    assert rotated.json()["secret"] == webhook_secret(installation, 1) != first.json()["secret"]
    assert (await client.post(route)).status_code == 409, "the new key was shown too"

    # The deliveries are signed with the CURRENT key, the one the owner was shown.
    assert (
        await client.patch(
            f"{ORG}/assistants/investment-assistant",
            json={"policy_overrides": {"delivery": {"webhook": {"url": "https://apps.test/hook"}}}},
        )
    ).status_code == 200
    run = (await mine.post(f"{TASKS}/morning_brief/run")).json()
    await _produce(run["work_item_id"], brief="Hello")
    received: list[httpx.Request] = []

    def receiver(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    set_a2a_transport(httpx.MockTransport(receiver))
    try:
        async with session_scope(orgs="*") as session:
            await deliver_pending(session)
    finally:
        set_a2a_transport(None)
    (request,) = received
    stamp, signature = re.fullmatch(
        r"t=(\d+),v1=([0-9a-f]{64})", request.headers["x-choregos-signature"]
    ).groups()  # type: ignore[union-attr]

    def signed(secret: str) -> str:
        return hmac.new(secret.encode(), stamp.encode() + b"." + request.content, hashlib.sha256).hexdigest()

    assert signature == signed(rotated.json()["secret"]) and signature != signed(first.json()["secret"])


async def test_a_conversation_is_offered_run_task_which_starts_the_task_as_the_person(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    from choregos_api.db.models import AgentSessionRecord, AssistantInstallation
    from choregos_api.db.session import session_scope
    from choregos_api.services.conversation_extras import conversation_extras

    from .kits.conversations import _open

    await _ready(client)
    await _subscriber(client, app, "mine@varga.dev")
    installation = await _installation_id()
    spec = {"instructions": "Help.", "runtime": {"kind": "prompt"}}
    made = await client.post(f"{ORG}/agents", json={"slug": "helper", "display_name": "h", "spec": spec})
    assert made.status_code == 201, made.text
    conversation = await _open("varga", "mine@varga.dev", installation_id=installation)
    async with session_scope(orgs="*") as session:
        record = await session.get(AgentSessionRecord, conversation)
        assert record is not None
        extras = await conversation_extras(session, record, [])
        (tool,) = [t for t in extras.loop_tools if t.name == "run_task"]
        assert tool.parameters["properties"]["task"]["enum"] == ["delegated_brief", "morning_brief"], (
            "the on-demand tasks of the manifest, `threshold_alert` (a poll) not among them"
        )
        assert extras.serves("run_task")
        told = await extras.call(session, "run_task", {"task": "delegated_brief"})
        assert "was started" in told
        refused = await extras.call(session, "run_task", {"task": "threshold_alert"})
        assert "task not started" in refused
        installation_row = await session.get(AssistantInstallation, installation)
        assert installation_row is not None
    runs = await _runs("mine@varga.dev")
    ((item, _),) = runs
    assert item.initiated_by == "person:mine@varga.dev"
    task = item.documents["task"]
    assert task["trigger"] == "on_demand" and task["delegation_id"] is None, "no delegation under a tool"
    assert task["task"] == "delegated_brief"


# ───────────────────────── the loop, the poll's journal (F2) ─────────────────────────


async def test_run_task_is_called_by_the_model_in_a_turn_and_starts_the_task_as_the_person(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
    upstream: Upstream,  # noqa: F811
) -> None:
    """A scripted model calls `run_task` in a full turn of `POST .../messages`: the tool is offered to the
    model, the work item opens in the person's space with the person as initiator, no delegation."""
    await _ready(client)
    mine = await _subscriber(client, app, "mine@varga.dev")
    await _subscriber(client, app, "other@varga.dev")
    opened = await mine.post(f"{ME}/assistants/varga/investment-assistant/conversations", json={})
    assert opened.status_code == 201, opened.text
    conversation = opened.json()["id"]
    upstream.model.call("run_task", {"task": "morning_brief"}).say("Your brief is on its way.")
    said = await mine.post(f"{ME}/conversations/{conversation}/messages", json={"input": "Brief me."})
    assert said.status_code == 200, said.text

    (_, first) = upstream.model.requests[0]
    assert "run_task" in {t["function"]["name"] for t in first["tools"]}
    (_, last) = upstream.model.requests[-1]
    told = [m["content"] for m in last["messages"] if m["role"] == "tool"]
    assert told and "was started" in told[0], told
    ((item, owner),) = await _runs()
    assert owner == "mine@varga.dev" and item.initiated_by == "person:mine@varga.dev"
    task = item.documents["task"]
    assert (task["task"], task["trigger"], task["delegation_id"]) == ("morning_brief", "on_demand", None)
    assert await _runs("other@varga.dev") == [], "nobody else's space got it"


async def _journal() -> list[Any]:
    from choregos_api.db.models import AssistantRead
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = list((await session.execute(select(AssistantRead).order_by(AssistantRead.at))).scalars())
        for row in rows:
            session.expunge(row)
        return rows


async def test_the_reads_of_a_poll_are_journaled_with_the_task_and_the_work_item_once_it_exists(
    client: AsyncClient,
    org: str,
    app: Any,
    offline: Path,  # noqa: F811
    markets: Any,  # noqa: F811
) -> None:
    await _ready(client)
    person = await _subscriber(client, app, "poller@varga.dev")
    assert (await person.put(f"{TASKS}/threshold_alert", json={"kind": "poll"})).status_code == 200

    markets.structured["get_quote"] = {"events": [{"id": "e1", "change_pct": 4.2}]}
    await _pass(at(9, 0))
    (first,) = await _journal()
    assert (first.task, first.need, first.operation) == (
        "threshold_alert",
        "market_data",
        "market_data__get_quote",
    )
    assert first.delegation_id is None and first.conversation_id is None
    ((item, _),) = await _runs()
    assert first.work_item_key == item.tracker_key, "one read, one run: the line names the work item"

    # Two objects start two runs from ONE read: the line names the task, not one of the items.
    markets.structured["get_quote"] = {
        "events": [
            {"id": "e1", "change_pct": 4.2},
            {"id": "e2", "change_pct": 5},
            {"id": "e3", "change_pct": 6},
        ]
    }
    await _pass(at(9, 15))
    lines = await _journal()
    assert len(lines) == 2, "one line per poll"
    assert lines[-1].task == "threshold_alert" and lines[-1].work_item_key is None
