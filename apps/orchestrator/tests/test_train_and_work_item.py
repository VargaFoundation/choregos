"""The train and the ticket talk to each other (ADR 0041, S21-21).

The review of 2026-10-07 measured it: the train never told the ticket it had delivered; the
ticket waited for a `cd.*` that nothing sent, and the tests carried it by hand; and the
approval that a workflow requires of the departure (`train.approval`) never reached the train.
Here, a REAL `ReleaseTrain` sends the batch off; nobody writes a CD event.
"""

from __future__ import annotations

import asyncio
import copy
import json
import pathlib
from datetime import UTC, datetime
from typing import Any

import pytest

from .conftest import Fixture, recording_history
from .kits.train_and_work_item import (
    run_work_item_to_train,
    set_prod_approval_policy,
    start_prod_train,
    wait_until_state,
)

pytestmark = pytest.mark.asyncio

CAPTAINS = {"required": True, "group": "release-captains"}
HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


async def _releases() -> list[Any]:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(Release))).scalars())


# ───────────────────────── the ticket that wakes the train (S22-22) ─────────────────────────


async def test_the_ticket_that_wakes_the_train_stays_in_the_batch(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The interpreter boards by signal-with-start: when no train is running, ITS signal starts it.
    `run` then overwrote the batch: #4 and #6 of the dev tenant (10/09) woke their train and were
    erased from it."""
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await temporal_env.client.start_workflow(
                "ReleaseTrain",
                {"project_slug": setup.project_slug, "env": "prod"},
                id=f"train-{setup.project_slug}-prod",
                task_queue="test",
                start_signal="merged",
                start_signal_args=[{"work_item_key": "varga/billing-api#4", "sha": "a4"}],
            )
            state = await wait_until_state(train, "status_query", lambda s: s["status"] == "collecting")
            assert state["pending_items"] == ["varga/billing-api#4"], state
            await train.signal("merged", {"work_item_key": "varga/billing-api#4", "sha": "a4"})
            state = await wait_until_state(train, "status_query", lambda s: s["status"] == "collecting")
            assert state["batch_size"] == 1, "a second boarding of the same ticket does not double it"
            await train.signal("abort", {"by": "test"})
            await train.result()


# ───────────────────────── the train: the approval follows what the batch carries ─────────────────────────


async def test_a_batch_without_requirement_leaves_without_approval_when_the_policy_asks_for_none(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await set_prod_approval_policy(setup, approval=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await start_prod_train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#1", "sha": "a1"})
            await train.signal("depart_now", {"by": "augustin"})
            await wait_until_state(
                train, "status_query", lambda s: s["status"] == "collecting" and s["release_id"]
            )
            await train.signal("abort", {"by": "test"})
            await train.result()
    (release,) = await _releases()
    assert release.status == "done" and release.approved_by is None


async def test_a_ticket_that_requires_approval_holds_the_whole_batch(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await set_prod_approval_policy(setup, approval=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await start_prod_train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#1", "sha": "a1"})
            await train.signal(
                "merged", {"work_item_key": "varga/billing-api#2", "sha": "a2", "approval": CAPTAINS}
            )
            await train.signal("depart_now", {"by": "augustin"})
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            assert not setup.adapters.cd.rollouts, "the batch left before the approval its ticket requires"
            await train.signal("approve", {"by": "marie"})
            await wait_until_state(
                train, "status_query", lambda s: s["status"] == "collecting" and s["batch_size"] == 0
            )
            await train.signal("abort", {"by": "test"})
            await train.result()
    (release,) = await _releases()
    assert release.status == "done" and release.approved_by == "marie"
    assert len(release.items) == 2, "the fix leaves with the feature, under the same approval"


async def test_the_requirement_brought_by_a_second_boarding_is_added(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The webhook boards the ticket without a requirement first, the interpreter then with one: the
    duplicate was ignored entirely, and the approval with it."""
    await set_prod_approval_policy(setup, approval=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await start_prod_train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#1", "sha": "a1"})
            await train.signal(
                "merged", {"work_item_key": "varga/billing-api#1", "sha": "a1", "approval": CAPTAINS}
            )
            await wait_until_state(train, "status_query", lambda s: s["batch_size"] == 1)
            await train.signal("depart_now", {"by": "augustin"})
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("abort", {"by": "test"})
            await train.result()


# ───────────────────────── the ticket: it advances when ITS train has delivered ─────────────────────────


async def test_the_ticket_advances_when_its_train_has_delivered_without_a_hand_carried_event(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await set_prod_approval_policy(setup, approval=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, boarding = await run_work_item_to_train(setup, temporal_env)
            assert boarding["approval"] == CAPTAINS, "the captain that `default-simple` names"

            train = await start_prod_train(temporal_env, setup)
            await train.signal("merged", boarding)
            await train.signal("depart_now", {"by": "augustin"})
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("approve", {"by": "marie"})
            issue = await asyncio.wait_for(ticket.result(), timeout=60)
            await train.signal("abort", {"by": "test"})
            await train.result()
            histories = {"wi-train-S21-21": await ticket.fetch_history(),
                           "train-approval-S21-21": await train.fetch_history()}  # fmt: skip
    assert issue["state"] == "deployed_prod"

    # The two histories replay against the current code, and are archived for those to come.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    for name, history in histories.items():
        await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
        if recording_history():
            target = HISTORIES / f"{name}.json"
            target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_an_event_from_another_environment_is_not_a_delivery(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, _ = await run_work_item_to_train(setup, temporal_env)
            await ticket.signal(
                "inbound",
                {"type": "cd.rollout.completed", "source": "release-train", "delivery_id": "staging-1",
                 "payload": {"env": "staging", "release_id": "r-staging"}},
            )  # fmt: skip
            await asyncio.sleep(1.5)
            assert (await ticket.query("status"))["state"] == "merged", "staging done is not prod"
            await ticket.signal(
                "inbound",
                {"type": "cd.rollout.completed", "source": "release-train", "delivery_id": "prod-1",
                 "payload": {"env": "prod", "release_id": "r-prod"}},
            )  # fmt: skip
            issue = await asyncio.wait_for(ticket.result(), timeout=60)
    assert issue["state"] == "deployed_prod"


async def test_a_rollback_hands_the_ticket_to_a_human_without_waiting_for_the_delay(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    setup.adapters.cd.fail_next_analysis()
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, boarding = await run_work_item_to_train(setup, temporal_env)
            train = await start_prod_train(temporal_env, setup)
            await train.signal("merged", boarding)
            await train.signal("depart_now", {"by": "augustin"})
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("approve", {"by": "marie"})
            await wait_until_state(train, "status_query", lambda s: s["frozen"] is True)
            await wait_until_state(ticket, "status", lambda s: s["state"] == "needs_human")
            await train.signal("abort", {"by": "test"})
            await train.result()
            await ticket.cancel()


# ───────────── the express lane: from the ticket, under ITS approval (#279) ─────────────

HOTFIX_CAPTAINS = {"group": "hotfix-captains", "required": True}
#: The case whose history is archived (`CHOREGOS_RECORD_HISTORY=1`). `train-express-old-code`
#: was recorded on `main` before this change, case `lane-waives`: it goes red if the decision
#: leaves the `express-lane-approval` marker.
EXPRESS_ARCHIVES = {"lane-requires": "train-express-S22-17"}


async def _express_lane(setup: Fixture, *, approval_required: bool, lane: dict[str, Any] | None) -> None:
    """The ordinary approval of prod, and that of its express lane (`None`: the lane says nothing)."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(
                    PolicyDef.project_id == setup.project_id, PolicyDef.is_active.is_(True)
                )
            )
        ).scalar_one()
        document = copy.deepcopy(row.json_doc)
        prod = document["release_train"]["prod"]
        prod["approval"]["required"] = approval_required
        if lane is None:
            prod["express_lane"].pop("approval", None)
        else:
            prod["express_lane"]["approval"] = lane
        row.json_doc = document


def _express_departures(history: Any) -> list[bool]:
    """Each departure of the train, express or not, as the history scheduled it (`create_release`)."""
    departures: list[bool] = []
    for event in history.events:
        attributes = event.activity_task_scheduled_event_attributes
        if event.HasField("activity_task_scheduled_event_attributes") and (
            attributes.activity_type.name == "create_release"
        ):
            departures.append(bool(json.loads(attributes.input.payloads[0].data).get("express")))
    return departures


async def _requested_groups() -> list[str | None]:
    """The group of each departure approval request, in order."""
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(
            select(Event).where(Event.type == "choregos.release.approval_requested").order_by(Event.ts)
        )
        return [(e.payload or {}).get("group") for e in rows.scalars()]


async def test_a_hotfix_ticket_boarded_by_the_interpreter_takes_the_express_lane(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`signal_train` boarded with `labels: []`: a hotfix coming from the interpreter waited for the cron."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import record_labels

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        record_labels(item, ["agent-ready", "hotfix"])
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, boarding = await run_work_item_to_train(setup, temporal_env)
            assert boarding["labels"] == ["agent-ready", "hotfix"], "the ticket boards its labels"

            train = await start_prod_train(temporal_env, setup)
            await train.signal("merged", boarding)  # no `depart_now`: the hotfix leaves alone
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("abort", {"by": "test"})
            await train.result()
            await ticket.cancel()
            history = await train.fetch_history()
    assert _express_departures(history) == [True], "left by the express lane, without waiting for the cron"


@pytest.mark.parametrize(
    ("approval_required", "lane", "ticket", "expected"),
    [
        # The policy asks for nothing, the express lane does: the hotfix waits for ITS captains.
        (False, HOTFIX_CAPTAINS, None, ["hotfix-captains"]),
        # The policy asks, the express lane does not: the hotfix leaves without waiting.
        (True, {"required": False}, None, []),
        # ... unless a ticket of the batch requires it (ADR 0041): the tickets' requirement always holds.
        (True, {"required": False}, CAPTAINS, ["release-captains"]),
        # An express lane that says nothing about approval keeps the policy's.
        (True, None, None, ["release-captains"]),
    ],
    ids=["lane-requires", "lane-waives", "ticket-requires", "lane-silent"],
)
async def test_an_express_departure_takes_the_approval_of_the_express_lane(
    setup: Fixture,
    temporal_env: Any,
    worker_factory: Any,
    approval_required: bool,
    lane: dict[str, Any] | None,
    ticket: dict[str, Any] | None,
    expected: list[str],
    request: pytest.FixtureRequest,
) -> None:
    await _express_lane(setup, approval_required=approval_required, lane=lane)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    boarding: dict[str, Any] = {"work_item_key": "varga/billing-api#9", "sha": "f1", "labels": ["hotfix"]}
    if ticket is not None:
        boarding["approval"] = ticket
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await start_prod_train(temporal_env, setup)
            await train.signal("merged", boarding)
            state = await wait_until_state(
                train,
                "status_query",
                lambda s: (
                    s["status"] == "awaiting_approval" or (s["status"] == "collecting" and s["release_id"])
                ),
            )
            if state["status"] == "awaiting_approval":
                await train.signal("approve", {"by": "marie"})
                await wait_until_state(train, "status_query", lambda s: s["status"] == "collecting")
            await train.signal("abort", {"by": "test"})
            await train.result()
            history = await train.fetch_history()
    # The history replays against the current code; the one of the lane that requires is archived.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    name = EXPRESS_ARCHIVES.get(request.node.callspec.id)
    if name and recording_history():
        target = HISTORIES / f"{name}.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
    assert _express_departures(history) == [True]
    (release,) = await _releases()
    assert release.status == "done"
    assert await _requested_groups() == expected


# ─────────────── the express lane reads ITS label, duplicates included (#351, S22-30) ───────────────

#: The cases whose history is archived (`CHOREGOS_RECORD_HISTORY=1`).
EXPRESS_LABEL_ARCHIVES = {
    "policy-label": "train-express-label-S22-30",
    "labelled-duplicate": "train-express-duplicate-S22-30",
    "held-by-schedule": "train-express-held-by-schedule-S22-30",
}


async def _prod_policy(setup: Fixture, **changes: Any) -> None:
    """Sets fields of the prod train policy; `express_lane__x` sets `express_lane.x`, `None` removes.

    Unless a test names one, the prod cron is set twelve hours away (S22-30b). The fixture's
    `0 10,14,17 * * 1-4` is due for a whole minute three times a day, Monday to Thursday, in UTC: a
    suite that ran at 10:00:43 on 2026-10-19 saw three trains that must wait for the cron leave on it.
    """
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(
                    PolicyDef.project_id == setup.project_id, PolicyDef.is_active.is_(True)
                )
            )
        ).scalar_one()
        document = copy.deepcopy(row.json_doc)
        prod = document["release_train"]["prod"]
        changes.setdefault("schedule", f"0 {(datetime.now(UTC).hour + 12) % 24} * * *")
        for key, value in changes.items():
            target, _, field = key.rpartition("__")
            place = prod[target] if target else prod
            if value is None:
                place.pop(field, None)
            else:
                place[field] = value
        row.json_doc = document


async def _express_label(setup: Fixture, label: str) -> None:
    """The prod express lane is named `label` (`express_lane.label`)."""
    await _prod_policy(setup, express_lane__label=label)


async def _replay_and_archive(name: str | None, history: Any) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if name and recording_history():
        target = HISTORIES / f"{name}.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_the_express_lane_is_recognised_by_the_policy_label(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`express_lane.label: urgent`: an `urgent` ticket departures on its own, without `depart_now` or cron.

    Before #351, `merged` compared against a hard-coded `hotfix`: the `urgent` ticket waited for cron."""
    await _express_label(setup, "urgent")
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await start_prod_train(temporal_env, setup)
            await train.signal(
                "merged", {"work_item_key": "varga/billing-api#11", "sha": "u1", "labels": ["urgent"]}
            )
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("abort", {"by": "test"})
            await train.result()
            history = await train.fetch_history()
    await _replay_and_archive(EXPRESS_LABEL_ARCHIVES["policy-label"], history)
    assert _express_departures(history) == [True], "departed on the express lane the policy names"


async def test_hotfix_does_not_take_an_express_lane_with_another_name(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`express_lane.label: urgent`: a `hotfix` ticket waits for the ordinary departure."""
    await _express_label(setup, "urgent")
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await start_prod_train(temporal_env, setup)
            await train.signal(
                "merged", {"work_item_key": "varga/billing-api#12", "sha": "h1", "labels": ["hotfix"]}
            )
            await wait_until_state(train, "status_query", lambda s: s["batch_size"] == 1)
            await asyncio.sleep(3)  # a hotfix taken for express would already have departed
            state = await train.query("status_query")
            await train.signal("abort", {"by": "test"})
            await train.result()
            history = await train.fetch_history()
    await _replay_and_archive(None, history)
    assert state["status"] == "collecting" and state["release_id"] is None, state
    assert _express_departures(history) == [], "no departure: `hotfix` is not the lane's label"


async def test_a_labelled_duplicate_boarding_triggers_the_express_lane(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The ticket boards first without a label, then comes back labelled `hotfix` (set between the two
    boardings): it departures on the express lane. Before #351, the duplicate branch merged the approval
    and returned without reading the labels."""
    # The cron away from now (S22-30b): when it is due, the ticket leaves on the schedule, not express,
    # before its labelled duplicate arrives (CI, Wednesday 2026-11-04 at 17:00 UTC).
    await _prod_policy(setup)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await start_prod_train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#13", "sha": "d1", "labels": []})
            await wait_until_state(train, "status_query", lambda s: s["batch_size"] == 1)
            # The configuration is loaded (the first window check, which follows it, set `next_departure`):
            # the duplicate goes through the signal. A fixed one-second sleep raced it on a busy runner.
            await wait_until_state(train, "status_query", lambda s: s["next_departure"] is not None)
            await train.signal(
                "merged", {"work_item_key": "varga/billing-api#13", "sha": "d1", "labels": ["hotfix"]}
            )
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("abort", {"by": "test"})
            await train.result()
            history = await train.fetch_history()
    await _replay_and_archive(EXPRESS_LABEL_ARCHIVES["labelled-duplicate"], history)
    assert _express_departures(history) == [True], "a single express departure of a single ticket"
    (release,) = await _releases()
    assert len(release.items) == 1, "the duplicate counts once"
    assert release.items[0].get("labels") == ["hotfix"], "the release lists the label set in between"


async def test_an_express_lane_that_keeps_the_schedule_holds_the_departure(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`express_lane.skip_schedule: false`: a `hotfix` ticket waits for the schedule (the prod cron is
    not due) instead of leaving at once; a human `depart_now` still sends it, on the express lane.

    Before #351, `skip_schedule` was read nowhere: the express lane always bypassed the schedule."""
    await _prod_policy(setup, express_lane__skip_schedule=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await start_prod_train(temporal_env, setup)
            await train.signal(
                "merged", {"work_item_key": "varga/billing-api#15", "sha": "s1", "labels": ["hotfix"]}
            )
            await wait_until_state(train, "status_query", lambda s: s["batch_size"] == 1)
            await asyncio.sleep(3)  # an express that ignores the schedule would already have departed
            held = await train.query("status_query")
            await train.signal("depart_now", {"by": "augustin"})
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("abort", {"by": "test"})
            await train.result()
            history = await train.fetch_history()
    await _replay_and_archive(EXPRESS_LABEL_ARCHIVES["held-by-schedule"], history)
    assert held["status"] == "collecting" and held["release_id"] is None, held
    assert _express_departures(history) == [True], "the human departure keeps the express lane"


async def test_an_express_lane_that_keeps_the_schedule_leaves_when_it_is_due(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`skip_schedule: false` and a cron due every minute, no window: the express departures on schedule."""
    await _prod_policy(setup, express_lane__skip_schedule=False, schedule="* * * * *", windows=None)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await start_prod_train(temporal_env, setup)
            await train.signal(
                "merged", {"work_item_key": "varga/billing-api#16", "sha": "s2", "labels": ["hotfix"]}
            )
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("abort", {"by": "test"})
            await train.result()
            history = await train.fetch_history()
    await _replay_and_archive(None, history)
    assert _express_departures(history) == [True]


# ───────────────────────── a train that delivers without Slack (S22-26) ─────────────────────────


async def test_the_train_delivers_and_notifies_the_ticket_without_a_notifier(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """On the dev tenant, on 10/10, without Slack: promotion and verification done, `finish_release`
    raised on the notification, and both trains died without notifying their tickets."""
    await set_prod_approval_policy(setup, approval=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    setup.adapters.notify.failure = "[slack] neither `bot_token` nor `webhook_url` configured"
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, boarding = await run_work_item_to_train(setup, temporal_env)
            train = await start_prod_train(temporal_env, setup)
            await train.signal("merged", boarding)
            await train.signal("depart_now", {"by": "augustin"})
            await wait_until_state(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("approve", {"by": "marie"})
            issue = await asyncio.wait_for(ticket.result(), timeout=60)
            await train.signal("abort", {"by": "test"})
            await train.result()
    assert issue["state"] == "deployed_prod"
    assert [release.status for release in await _releases()] == ["done"]
    assert setup.adapters.notify.sent == []
