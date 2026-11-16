# SPDX-License-Identifier: Apache-2.0
"""A person settles an action in doubt (S65-26, ADR 0149 §5).

A write sent through an outpost and never answered leaves its action `in_doubt`, and the platform does
nothing more by itself. A person checks the system the write was for and records what they found:
`made` (the effect is `done` with the result they state, and the action resumes after it) or
`not_made` (the effect is `not_made`, never attempted, and the action fails, compensating what came
before). The route is guarded as an approval is; the effect's journal keeps the doubt's evidence; a
second settlement with the same outcome changes nothing, another is a conflict.

The outpost's side is the scripted daemon at the real door in front of `FakeOutpost`, as in
`test_write_via_outpost.py`; the action is played by the orchestrator's own activities and, where the
test says so, by the `ActionWorkflow` on a Temporal test server started through the API's own client.
They run on SQLite and, with `CHOREGOS_TEST_SUITE_ON_POSTGRES`, on PostgreSQL under row-level security.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.exceptions import ApplicationError

from .conftest import login
from .kits import outpost_route as route
from .kits import outpost_writes as kit
from .kits import outposts as outposts_kit
from .kits.outpost_route import SLUG
from .kits.outpost_writes import ACCEPTED

QUEUE = "choregos-settle-in-doubt"
TITLE = {"title": "door stuck"}
CHANGED = {"title": "door stuck, twice"}
UNDO = {"operation": "delete_issue", "arguments": {"key": "{{ result.key }}"}}
#: The third effect cites what the second returned: after `made`, what the person stated.
FOLLOW_UP = {"title": "follow-up of {{ effects[1].key }}"}
#: What the person read in the tracker, for the write whose answer never came.
FOUND = {"key": "OPS-8", "changed": True}
NOTE = "OPS-8 is in the tracker, changed at 10:02 by the outpost's service account"
OPS = "ops@varga.dev"


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> outposts_kit.Door:
    return route.switch_on(monkeypatch)


async def _member(client: AsyncClient, email: str, role: str) -> None:
    response = await client.post("/api/v1/orgs/varga/members", json={"email": email, "role": role})
    assert response.status_code in {200, 201}, response.text


@pytest.fixture
async def ops(client: AsyncClient, app: Any) -> AsyncIterator[AsyncClient]:
    """Another administrator of the organisation: the agent that proposed the actions is the first
    one's, and whoever owns the proposing agent does not settle (the separation of duties)."""
    await _member(client, OPS, "org_admin")
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(other, OPS)
    try:
        yield other
    finally:
        await other.aclose()


def _effects() -> list[dict[str, Any]]:
    return [
        kit.call("create_issue", TITLE, compensate=UNDO),
        kit.call("update_issue", CHANGED),
        kit.call("create_issue", FOLLOW_UP),
    ]


def _served_too_late(served: Any, fake: Any) -> None:
    def answered_too_late(_arguments: dict[str, Any]) -> dict[str, Any]:
        fake.lose_lease()  # the server makes the write, and its answer never leaves the outpost
        return {"key": "OPS-8", "changed": True}

    served.gestures["update_issue"] = answered_too_late


async def _prepared(client: AsyncClient, project: dict[str, Any]) -> tuple[Any, Any, Any]:
    served, fake = kit.outpost()
    opened = dict.fromkeys(("create_issue", "update_issue", "delete_issue"), ACCEPTED)
    daemon, _headers = await kit.prepared(client, project, fake, opened=opened)
    _served_too_late(served, fake)
    return served, fake, daemon


async def _in_doubt(client: AsyncClient, project: dict[str, Any]) -> tuple[Any, Any, Any, str]:
    """An action whose first write was answered and whose second was claimed and never answered,
    closed `in_doubt` as its workflow closes it."""
    from choregos_orchestrator.activities.actions import close_action

    served, fake, daemon = await _prepared(client, project)
    action_id = await kit.action(project["id"], _effects())
    async with route.serving(client, daemon, fake):
        await kit.play(action_id, 0)
        with pytest.raises(ApplicationError) as doubt:
            await kit.play(action_id, 1)
    assert doubt.value.type == "EffectInDoubt"
    await close_action({"action_id": action_id, "status": "in_doubt", "error": str(doubt.value)})
    assert (await kit.action_row(action_id)).status == "in_doubt"
    return served, fake, daemon, action_id


def _path(project: dict[str, Any], action_id: str) -> str:
    return f"/api/v1/projects/{project['id']}/actions/{action_id}/settlement"


async def _events(action_id: str, kind: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        rows = await session.execute(select(Event).where(Event.subject == action_id, Event.type == kind))
        return [dict(row.payload or {}) for row in rows.scalars()]


def _fake_temporal() -> Any:
    from choregos_api.temporal import FakeTemporal, get_temporal

    fake = get_temporal()
    assert isinstance(fake, FakeTemporal)
    return fake


# ───────────────────────── what each outcome records ─────────────────────────


async def test_made_records_the_effect_done_with_the_stated_result_and_the_action_resumes(
    client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    from choregos_orchestrator.activities.actions import close_action

    served, fake, daemon, action_id = await _in_doubt(client, project)
    (_done, doubted) = await kit.journal(action_id)
    evidence = list(doubted.result["in_doubt"])
    temporal = _fake_temporal()
    before = len(temporal.action_restarts)

    settled = await ops.post(
        _path(project, action_id), json={"outcome": "made", "note": NOTE, "result": FOUND}
    )
    assert settled.status_code == 200, settled.text
    shown = settled.json()
    assert (shown["status"], shown["error"], shown["finished_at"]) == ("approved", None, None)
    record = shown["settlement"]
    assert (record["position"], record["outcome"], record["by"], record["note"]) == (1, "made", OPS, NOTE)
    assert record["result"] == FOUND and record["at"] and record["auth_age_seconds"] is not None
    # The journal keeps the doubt's evidence, with the settlement, on the effect it concerns.
    assert record["in_doubt"] == evidence and evidence[0]["outpost"] == SLUG
    assert [(e["status"], e["settlement"] is not None) for e in shown["journal"]] == [
        ("done", False),
        ("done", True),
    ]
    assert shown["journal"][1]["result"] == FOUND and shown["journal"][1]["settlement"] == record
    # The workflow of the action leaves after the commit, under its own id, once more.
    assert temporal.action_restarts[before:] == [f"action-{action_id}"]
    # What it said, to the audit and to the events.
    (audited,) = await outposts_kit.audit_rows("action.settle")
    assert (audited.target_id, audited.payload["outcome"], audited.payload["position"]) == (
        action_id,
        "made",
        1,
    )
    (event,) = await _events(action_id, "choregos.action.settled")
    assert (event["outcome"], event["position"], event["by"]) == ("made", 1, OPS)

    # The run that follows, as the workflow plays it: the journal answers what was done or settled,
    # and the effect after it is played, citing what the person stated.
    async with route.serving(client, daemon, fake):
        assert (await kit.play(action_id, 0))["replayed"] is True
        replayed = await kit.play(action_id, 1)
        assert (replayed["replayed"], replayed["result"]) == (True, FOUND)
        assert (await kit.play(action_id, 2))["replayed"] is False
    await close_action({"action_id": action_id, "status": "succeeded"})
    assert served.calls == [
        ("create_issue", TITLE),
        ("update_issue", CHANGED),
        ("create_issue", {"title": "follow-up of OPS-8"}),
    ], "the write in doubt was not sent again"
    assert (await kit.action_row(action_id)).status == "succeeded"


async def test_not_made_is_never_attempted_and_the_action_compensates_what_came_before(
    client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    from choregos_orchestrator.activities.actions import close_action, compensate_effect

    served, fake, daemon, action_id = await _in_doubt(client, project)
    note = "nothing changed in the tracker since 10:00"
    settled = await ops.post(_path(project, action_id), json={"outcome": "not_made", "note": note})
    assert settled.status_code == 200, settled.text
    shown = settled.json()
    assert shown["status"] == "approved" and shown["settlement"]["outcome"] == "not_made"
    assert shown["settlement"]["result"] is None and shown["settlement"]["in_doubt"]
    (_done, not_made) = shown["journal"]
    assert (not_made["status"], not_made["result"]) == ("not_made", None)
    assert OPS in not_made["error"] and note in not_made["error"]

    async with route.serving(client, daemon, fake):
        assert (await kit.play(action_id, 0))["replayed"] is True
        # The effect a person recorded not made is a refusal, final, without a call.
        with pytest.raises(ApplicationError) as refused:
            await kit.play(action_id, 1)
        assert (refused.value.type, refused.value.non_retryable) == ("EffectRefused", True)
        assert note in str(refused.value)
        # The failure path: what was done before it is undone; nothing is in doubt any more.
        undone = await compensate_effect({"action_id": action_id, "position": 0})
        assert undone == {"key": f"{action_id}:0", "undone": True}, undone
    closed = await close_action({"action_id": action_id, "status": "failed", "error": str(refused.value)})
    assert closed["status"] == "failed", "closed as it asked: no effect is in doubt any more"
    assert served.calls == [
        ("create_issue", TITLE),
        ("update_issue", CHANGED),
        ("delete_issue", {"key": "OPS-7"}),
    ], "the write in doubt was not sent again, and the first one was undone"
    effects = await kit.journal(action_id)
    assert [row.status for row in effects] == ["compensated", "not_made"]


# ───────────────────────── who may settle ─────────────────────────


async def test_whoever_owns_the_proposing_agent_does_not_settle(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    _served, _fake, _daemon, action_id = await _in_doubt(client, project)
    refused = await client.post(_path(project, action_id), json={"outcome": "made", "note": NOTE})
    assert refused.status_code == 422 and "separation of duties" in refused.text, refused.text
    assert "does not settle" in refused.text
    assert (await kit.action_row(action_id)).status == "in_doubt"


async def test_a_developer_and_an_api_token_do_not_settle(
    client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    _served, _fake, _daemon, action_id = await _in_doubt(client, project)
    await _member(client, "dev@varga.dev", "developer")
    dev = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(dev, "dev@varga.dev")
    try:
        refused = await dev.post(_path(project, action_id), json={"outcome": "made", "note": NOTE})
        assert refused.status_code == 403 and "approver's rank" in refused.text, refused.text
    finally:
        await dev.aclose()
    token = (await client.post("/api/v1/me/tokens", json={"name": "ci", "scopes": ["*"]})).json()["token"]
    by_token = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        refused = await by_token.post(
            _path(project, action_id),
            json={"outcome": "made", "note": NOTE},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert refused.status_code == 403 and "human session" in refused.text, refused.text
    finally:
        await by_token.aclose()
    assert (await kit.action_row(action_id)).status == "in_doubt"


async def test_a_group_the_rule_names_is_required_to_settle_as_to_approve(
    client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    _served, _fake, _daemon, action_id = await _in_doubt(client, project)
    async with session_scope(orgs="*") as session:
        row = await session.get(Action, action_id)
        assert row is not None
        row.approval = {"approvers": [{"role": "project_owner", "group": "tracker-approvers", "min": 1}]}
    refused = await ops.post(_path(project, action_id), json={"outcome": "made", "note": NOTE})
    assert refused.status_code == 403 and "tracker-approvers" in refused.text, refused.text


async def test_a_session_that_is_too_old_must_authenticate_again(
    client: AsyncClient, project: dict[str, Any], ops: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api import freshness

    _served, _fake, _daemon, action_id = await _in_doubt(client, project)
    later = time.time() + 11 * 60
    monkeypatch.setattr(freshness.time, "time", lambda: later)
    refused = await ops.post(_path(project, action_id), json={"outcome": "made", "note": NOTE})
    assert refused.status_code == 401, refused.text
    assert refused.json()["errors"][0]["error"] == "step_up_required"
    assert (await kit.action_row(action_id)).status == "in_doubt"


# ───────────────────────── what is refused, and settling twice ─────────────────────────


async def test_a_note_is_required_and_a_result_goes_with_made_only(
    client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    _served, _fake, _daemon, action_id = await _in_doubt(client, project)
    path = _path(project, action_id)
    assert (await ops.post(path, json={"outcome": "made"})).status_code == 422
    assert (await ops.post(path, json={"outcome": "made", "note": ""})).status_code == 422
    assert (await ops.post(path, json={"outcome": "maybe", "note": NOTE})).status_code == 422
    with_result = await ops.post(path, json={"outcome": "not_made", "note": NOTE, "result": FOUND})
    assert with_result.status_code == 422 and "`result` goes with `made` only" in with_result.text
    heavy = await ops.post(path, json={"outcome": "made", "note": NOTE, "result": {"x": "y" * 17_000}})
    assert heavy.status_code == 422 and "16 KiB" in heavy.text
    assert (await kit.action_row(action_id)).status == "in_doubt"


async def test_settling_twice_with_the_same_outcome_changes_nothing_and_the_other_is_a_conflict(
    client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    _served, _fake, _daemon, action_id = await _in_doubt(client, project)
    path = _path(project, action_id)
    temporal = _fake_temporal()
    first = await ops.post(path, json={"outcome": "made", "note": NOTE, "result": FOUND})
    assert first.status_code == 200, first.text
    starts = len(temporal.action_restarts)
    again = await ops.post(path, json={"outcome": "made", "note": "clicked twice"})
    assert again.status_code == 200, again.text
    assert again.json()["settlement"] == first.json()["settlement"], "the first record stands"
    assert len(temporal.action_restarts) == starts, "no second start"
    assert len(await _events(action_id, "choregos.action.settled")) == 1
    assert len(await outposts_kit.audit_rows("action.settle")) == 1
    other = await ops.post(path, json={"outcome": "not_made", "note": "changed my mind"})
    assert other.status_code == 409 and other.json()["code"] == "action_already_settled", other.text
    assert "made" in other.json()["detail"] and OPS in other.json()["detail"]


async def test_an_action_that_is_not_in_doubt_is_not_settled(
    client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    await _prepared(client, project)
    for status in ("approved", "succeeded", "failed", "pending_approval"):
        action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)], status=status)
        refused = await ops.post(_path(project, action_id), json={"outcome": "made", "note": NOTE})
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "action_not_in_doubt" and status in refused.json()["detail"]
    missing = await ops.post(_path(project, "no-such-action"), json={"outcome": "made", "note": NOTE})
    assert missing.status_code == 404


async def test_an_effect_in_doubt_that_its_workflow_still_retries_is_not_settled(
    client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    """An operation that takes the platform's idempotency key is delivered again after a doubt: its
    effect's row stays `in_doubt` while the action is still `running`. The workflow is not done with
    it, and a person does not settle what the platform is still trying."""
    from choregos_api.db.models import Action, ActionEffect
    from choregos_api.db.session import session_scope

    await _prepared(client, project)
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)], status="running")
    async with session_scope(orgs="*") as session:
        action = await session.get(Action, action_id)
        assert action is not None
        session.add(
            ActionEffect(
                org_id=action.org_id,
                action_id=action_id,
                position=0,
                key=f"{action_id}:0",
                effect="connector.call",
                params={},
                status="in_doubt",
                attempts=1,
                result={"in_doubt": [{"outpost": SLUG}], "again": True},
            )
        )
    refused = await ops.post(_path(project, action_id), json={"outcome": "made", "note": NOTE})
    assert refused.status_code == 409 and refused.json()["code"] == "action_not_in_doubt", refused.text
    (effect,) = await kit.journal(action_id)
    assert (effect.status, effect.settlement) == ("in_doubt", None)


async def test_an_evidence_waited_for_before_the_settled_effect_is_not_asked_again(
    client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    """The run that follows a settlement answers the effects already done from the journal. One of
    them asked the workflow to wait for its evidence, and the run before waited for it: the replay
    does not ask for it again, or the action would wait for an evidence that already came."""
    from choregos_api.db.models import ActionEffect
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    _served, _fake, _daemon, action_id = await _in_doubt(client, project)
    waited = {"key": "OPS-7", "await_evidence": {"until": "2026-11-14T12:00:00+00:00"}}
    async with session_scope(orgs="*") as session:
        await session.execute(
            update(ActionEffect).where(ActionEffect.key == f"{action_id}:0").values(result=waited)
        )
    before = await kit.play(action_id, 0)
    assert "await_evidence" in before["result"], "while nothing is settled, the journal is answered as it is"
    # A settled action is approved again: `kit.play` reads its rows as the next run's activity does.
    assert (
        await ops.post(_path(project, action_id), json={"outcome": "made", "note": NOTE})
    ).status_code == 200
    after = await kit.play(action_id, 0)
    assert after == {"key": f"{action_id}:0", "replayed": True, "result": {"key": "OPS-7"}}


# ───────────────────────── the whole chain, on Temporal ─────────────────────────


@pytest.fixture
async def temporal() -> AsyncIterator[Any]:
    from temporalio.testing import WorkflowEnvironment

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        yield environment
    finally:
        await environment.shutdown()


def _workers(temporal: Any) -> Any:
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.testing import workers_by_queue
    from choregos_orchestrator.workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES

    return workers_by_queue(
        temporal.client,
        activities=[*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES],
        workflows=ALL_WORKFLOWS,
        workflow_queue=QUEUE,
    )


async def _settled_on_temporal(
    temporal: Any,
    client: AsyncClient,
    project: dict[str, Any],
    ops: AsyncClient,
    body: dict[str, Any],
    *,
    start_lost: bool = False,
) -> tuple[Any, str, dict[str, Any]]:
    """The action ends in doubt in its workflow; a person settles it through the API, whose own client
    starts the workflow again; the run that follows ends. Returns what the server received, the action
    and what the second run returned. `start_lost`: the start that follows the settlement's commit is
    lost (the API's client reaches no Temporal), and the relay starts it."""
    from choregos_api.services.action_relay import start_approved_actions
    from choregos_api.temporal import FakeTemporal, RealTemporal, set_temporal

    served, fake, daemon = await _prepared(client, project)
    action_id = await kit.action(project["id"], _effects())
    address = temporal.client.service_client.config.target_host
    set_temporal(RealTemporal(address, temporal.client.namespace, QUEUE))
    workflow_id = f"action-{action_id}"
    async with route.serving(client, daemon, fake), _workers(temporal):
        first = await temporal.client.start_workflow(
            "ActionWorkflow", {"action_id": action_id}, id=workflow_id, task_queue=QUEUE
        )
        ended = await asyncio.wait_for(first.result(), timeout=60)
        assert ended["status"] == "in_doubt", ended
        real = RealTemporal(address, temporal.client.namespace, QUEUE)
        if start_lost:
            set_temporal(FakeTemporal())
        settled = await ops.post(_path(project, action_id), json=body)
        assert settled.status_code == 200, settled.text
        if start_lost:
            set_temporal(real)
            assert (await kit.action_row(action_id)).status == "approved", "the row is the outbox"
            assert await start_approved_actions(grace=timedelta(0)) == [action_id]
        # The latest run of the same id: the one the settlement started, not the one that ended.
        latest = temporal.client.get_workflow_handle(workflow_id)
        description = await latest.describe()
        assert description.run_id != first.result_run_id, "a second run of `action-<id>`"
        result = await asyncio.wait_for(latest.result(), timeout=60)
    return served, action_id, result


async def test_on_temporal_made_runs_the_rest_of_the_action_and_it_succeeds(
    temporal: Any, client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    served, action_id, result = await _settled_on_temporal(
        temporal, client, project, ops, {"outcome": "made", "note": NOTE, "result": FOUND}
    )
    assert result == {"status": "succeeded", "effects": [0, 1, 2]}, result
    assert served.calls == [
        ("create_issue", TITLE),
        ("update_issue", CHANGED),
        ("create_issue", {"title": "follow-up of OPS-8"}),
    ]
    action = await kit.action_row(action_id)
    assert (action.status, action.error) == ("succeeded", None)
    assert [(row.status, row.attempts) for row in await kit.journal(action_id)] == [
        ("done", 1),
        ("done", 1),
        ("done", 1),
    ]
    assert len(await _events(action_id, "choregos.action.succeeded")) == 1


async def test_on_temporal_not_made_fails_the_action_and_compensates_what_came_before(
    temporal: Any, client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    served, action_id, result = await _settled_on_temporal(
        temporal, client, project, ops, {"outcome": "not_made", "note": "nothing in the tracker"}
    )
    assert result["status"] == "failed" and "nothing in the tracker" in result["error"], result
    assert [entry["undone"] for entry in result["compensations"]] == [True]
    assert served.calls == [
        ("create_issue", TITLE),
        ("update_issue", CHANGED),
        ("delete_issue", {"key": "OPS-7"}),
    ]
    action = await kit.action_row(action_id)
    assert action.status == "failed"
    assert [row.status for row in await kit.journal(action_id)] == ["compensated", "not_made"]
    failed = await _events(action_id, "choregos.action.failed")
    assert [event["status"] for event in failed] == ["in_doubt", "failed"]


async def test_on_temporal_the_relay_restarts_a_settled_action_whose_start_was_lost(
    temporal: Any, client: AsyncClient, project: dict[str, Any], ops: AsyncClient
) -> None:
    """The settlement commits and its start is lost: the `approved` row is the outbox (ADR 0085). The
    relay finds it and, the action's first run having completed, restarts the same id instead of
    taking Temporal's refusal for a start."""
    served, action_id, result = await _settled_on_temporal(
        temporal, client, project, ops, {"outcome": "made", "note": NOTE, "result": FOUND}, start_lost=True
    )
    assert result == {"status": "succeeded", "effects": [0, 1, 2]}, result
    assert len(served.calls) == 3 and (await kit.action_row(action_id)).status == "succeeded"
