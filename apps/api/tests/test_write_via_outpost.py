# SPDX-License-Identifier: Apache-2.0
"""A write through an outpost is delivered at most once, and may end in doubt (S65-14, ADR 0149 §5-6).

The platform does not see an outpost's server. Once an instance has claimed a write, an answer that
never comes leaves one fact unknown: whether the server executed it. So the write is placed once,
under an id derived from the key of the governed effect that plays it; whatever is retried meets
that first call; and a claimed write whose answer does not come ends `in_doubt`, on the effect and
on the action: terminal, never replayed, never compensated automatically. A write nobody claimed
reached no server, and only that one is retried. The one exception is an operation whose rule says
it takes the platform's idempotency key: it may be delivered again with the same key.

None of that is implied by silence (ADR 0101): a write of a connector at an outpost is opened only
when its rule says one of the two things, and the refusal of a rule that says neither names both.

The outpost's side is a scripted daemon at the real door, in front of the scriptable `FakeOutpost`
(`kits/outpost_route.py`, `kits/outpost_writes.py`); the rendezvous is the real one, over the
database; the action is played by the orchestrator's own activities, and where the test says so by
the `ActionWorkflow` on a Temporal test server. They run on SQLite and, with
`CHOREGOS_TEST_SUITE_ON_POSTGRES`, on PostgreSQL under row-level security.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import choregos_adapters
import pytest
from choregos_contracts import arguments_sha256
from httpx import AsyncClient
from temporalio.exceptions import ApplicationError

from .kits import outpost_route as route
from .kits import outpost_writes as kit
from .kits import outposts as outposts_kit
from .kits.outpost_route import ALIAS, BASE, CONNECTOR, ORG, SLUG
from .kits.outpost_writes import ACCEPTED, KEYED

QUEUE = "choregos-outpost-writes"
#: Seconds the broker waits for a governed write in the tests that play one on Temporal.
WAIT_S = 40
TITLE = {"title": "door stuck"}


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> outposts_kit.Door:
    return route.switch_on(monkeypatch)


def _detail(refused: Any) -> str:
    assert refused.status_code == 422, refused.text
    return str(refused.json()["detail"])


def _fields(refused: Any) -> list[str]:
    return sorted(str(error["loc"][-1]) for error in refused.json()["errors"])


# ───────────────────────── the rule when a write is opened ─────────────────────────


@pytest.mark.parametrize("policy", ["allowed", "approval"])
async def test_opening_a_write_with_neither_statement_is_a_422_that_names_both(
    client: AsyncClient, project: dict[str, Any], policy: str
) -> None:
    """ADR 0149 §6, ADR 0101: at most once, and maybe in doubt, is not implied by silence."""
    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake)
    refused = await kit.state(client, "create_issue", policy=policy)
    detail = _detail(refused)
    assert "`platform_arguments`" in detail and "`action_key`" in detail, detail
    assert "`in_doubt_accepted`" in detail, detail
    assert "at most once" in detail and "ADR 0149" in detail and "outpost" in detail, detail
    assert _fields(refused) == ["in_doubt_accepted", "platform_arguments"], "the refusal names both"
    operation = await kit.operation_of(client, "create_issue")
    assert (operation["policy"], operation["platform_arguments"], operation["in_doubt_accepted"]) == (
        "forbidden",
        [],
        False,
    )
    # Saying "no" to both is saying neither.
    assert (
        await kit.state(client, "create_issue", policy=policy, platform_arguments=[], in_doubt_accepted=False)
    ).status_code == 422
    # What does not open it still applies to it, and a read is opened like any other.
    assert (await kit.state(client, "create_issue", policy="forbidden", price_usd=0.5)).status_code == 200
    assert (await kit.state(client, "get_issue", policy=policy)).status_code == 200


@pytest.mark.parametrize("policy", ["allowed", "approval"])
async def test_a_write_is_opened_once_its_rule_accepts_an_in_doubt_outcome(
    client: AsyncClient, project: dict[str, Any], policy: str
) -> None:
    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake)
    opened = await kit.state(client, "create_issue", policy=policy, in_doubt_accepted=True)
    assert opened.status_code == 200, opened.text
    assert (opened.json()["policy"], opened.json()["in_doubt_accepted"]) == (policy, True)
    operation = await kit.operation_of(client, "create_issue")
    assert (operation["policy"], operation["in_doubt_accepted"], operation["platform_arguments"]) == (
        policy,
        True,
        [],
    )
    # The statement stays when something else of the rule changes.
    assert (await kit.state(client, "create_issue", price_usd=0.1)).json()["in_doubt_accepted"] is True


async def test_a_write_is_opened_once_its_rule_lists_the_idempotency_key(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The same words a plugin server uses for its own operations (`platform_arguments`), said by the
    organisation for a discovered tool. The tool must have the argument: a key its server would never
    read promises nothing."""
    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake)
    opened = await kit.state(client, "create_ticket", policy="allowed", platform_arguments=["action_key"])
    assert opened.status_code == 200, opened.text
    assert (opened.json()["platform_arguments"], opened.json()["in_doubt_accepted"]) == (
        ["action_key"],
        False,
    )
    # `create_issue` has no such argument: the statement is refused, and names what the tool has.
    refused = await kit.state(client, "create_issue", policy="allowed", platform_arguments=["action_key"])
    assert "no argument `action_key`" in _detail(refused) and "title" in _detail(refused)
    assert _fields(refused) == ["platform_arguments"]
    assert (await kit.operation_of(client, "create_issue"))["policy"] == "forbidden"
    # Another platform argument is not the idempotency key: alone, it opens nothing.
    assert (
        await kit.state(client, "create_ticket", policy="forbidden", platform_arguments=[])
    ).status_code == 200
    other = await kit.state(client, "create_ticket", policy="allowed", platform_arguments=["work_item_key"])
    assert _fields(other) == ["in_doubt_accepted", "platform_arguments"], other.text
    both = ["work_item_key", "action_key"]
    listed = await kit.state(client, "create_ticket", policy="allowed", platform_arguments=both)
    assert listed.status_code == 200 and listed.json()["platform_arguments"] == sorted(both), listed.text
    unknown = await kit.state(client, "create_ticket", platform_arguments=["idempotency_key"])
    assert unknown.status_code == 422, "the list is the contract's, closed"
    twice = await kit.state(client, "create_ticket", platform_arguments=["action_key", "action_key"])
    assert twice.status_code == 422


@pytest.mark.parametrize("statement", [ACCEPTED, KEYED], ids=["a doubt accepted", "the key"])
async def test_a_statement_made_while_the_write_is_closed_opens_it_later(
    client: AsyncClient, project: dict[str, Any], statement: dict[str, Any]
) -> None:
    """Two requests: what the rule says first, the policy after. The rule that is judged is the row's."""
    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake)
    said = await kit.state(client, "create_ticket", **statement)
    assert said.status_code == 200 and said.json()["policy"] == "forbidden", said.text
    opened = await kit.state(client, "create_ticket", policy="approval")
    assert opened.status_code == 200, opened.text
    assert opened.json()["policy"] == "approval"
    assert {name: opened.json()[name] for name in statement} == statement


async def test_withdrawing_the_last_statement_of_an_open_write_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The rule is judged as a change would leave it: an open write never holds neither statement."""
    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake, opened={"create_ticket": ACCEPTED})
    for withdrawn in ({"in_doubt_accepted": False}, {"in_doubt_accepted": None}):
        refused = await kit.state(client, "create_ticket", **withdrawn)
        assert _fields(refused) == ["in_doubt_accepted", "platform_arguments"], withdrawn
    assert (await kit.operation_of(client, "create_ticket"))["in_doubt_accepted"] is True
    # One statement for the other, in one change.
    swapped = await kit.state(client, "create_ticket", in_doubt_accepted=False, **KEYED)
    assert swapped.status_code == 200, swapped.text
    assert (swapped.json()["platform_arguments"], swapped.json()["in_doubt_accepted"]) == (
        ["action_key"],
        False,
    )
    for cleared in ([], None):
        refused = await kit.state(client, "create_ticket", platform_arguments=cleared)
        assert _fields(refused) == ["in_doubt_accepted", "platform_arguments"], cleared
    # Closed first, a write says nothing of how it would be delivered.
    closed = await kit.state(client, "create_ticket", policy="forbidden", platform_arguments=None)
    assert closed.status_code == 200, closed.text
    assert (closed.json()["policy"], closed.json()["platform_arguments"]) == ("forbidden", [])


async def test_a_statement_the_platform_would_not_act_on_is_refused(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0101: an in-doubt outcome exists for a write through an outpost and for nothing else, and
    the platform fills its arguments on a governed write."""
    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake)
    on_a_read = await kit.state(client, "get_issue", in_doubt_accepted=True)
    assert "nothing to accept" in _detail(on_a_read) and _fields(on_a_read) == ["in_doubt_accepted"]
    key_on_a_read = await kit.state(client, "search_issues", platform_arguments=["action_key"])
    assert "a read takes none" in _detail(key_on_a_read)
    # A direct connector's write is retried, never in doubt: there is nothing to accept there either.
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", route.server(kit.TOOLS))
    declared = await client.post(
        f"{ORG}/connectors",
        json={"name": "direct", "type": "mcp", "config": {"url": "https://direct.test/mcp"}},
    )
    assert declared.status_code == 201, declared.text
    assert (await client.post(f"{ORG}/connectors/direct/discover")).status_code == 200
    direct = f"{ORG}/connectors/direct/operations"
    accepted = await client.patch(f"{direct}/create_issue", json={"in_doubt_accepted": True})
    assert "nothing to accept" in _detail(accepted)
    # It is opened as it always was, with no statement, and may take the platform's key.
    for policy in ("approval", "allowed"):
        assert (await client.patch(f"{direct}/create_issue", json={"policy": policy})).status_code == 200
    keyed = await client.patch(f"{direct}/create_ticket", json={"policy": "allowed", **KEYED})
    assert keyed.status_code == 200 and keyed.json()["platform_arguments"] == ["action_key"], keyed.text


async def test_an_operation_its_type_declares_takes_its_platform_arguments_from_there(
    client: AsyncClient, admin: str
) -> None:
    """A rule says what the platform fills for a discovered tool only: a declaration is not said
    again, nor otherwise."""
    from .kits import netops

    created = await client.post(f"{ORG}/connectors", json={"name": "pfsense", **netops.CONNECTORS["pfsense"]})
    assert created.status_code == 201, created.text
    rule = f"{ORG}/connectors/pfsense/operations/create_firewall_rule"
    refused = await client.patch(rule, json={"platform_arguments": ["work_item_key"]})
    assert "type declares what the platform fills" in _detail(refused)
    assert _fields(refused) == ["platform_arguments"]
    unchanged = await client.patch(rule, json={"price_usd": 0.1})
    assert unchanged.status_code == 200 and unchanged.json()["platform_arguments"] == [], unchanged.text


async def test_a_drift_withdraws_what_was_stated(client: AsyncClient, project: dict[str, Any]) -> None:
    """A tool whose schema drifts is closed again (ADR 0034): what was stated of its delivery was
    said of the operation that was. Whoever opens it again says it again."""
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_ticket": KEYED})
    ticket = next(tool for tool in served.tools if tool["name"] == "create_ticket")
    ticket["inputSchema"] = {"type": "object", "properties": {"title": {"type": "string"}}}
    async with route.serving(client, daemon, fake):
        again = await client.post(f"{ORG}/connectors/{CONNECTOR}/discover")
    assert again.status_code == 200 and again.json()["changed"] == ["create_ticket"], again.text
    operation = await kit.operation_of(client, "create_ticket")
    assert (operation["policy"], operation["platform_arguments"], operation["in_doubt_accepted"]) == (
        "forbidden",
        [],
        False,
    )
    assert (await kit.state(client, "create_ticket", policy="allowed")).status_code == 422
    # The key it no longer takes cannot be stated of it.
    assert "no argument `action_key`" in _detail(await kit.state(client, "create_ticket", **KEYED))


async def test_the_statements_are_audited_with_who_made_them(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    (row,) = [
        row
        for row in await outposts_kit.audit_rows("org.connector.operation")
        if row.payload.get("operation") == "create_issue"
    ]
    assert row.payload["in_doubt_accepted"] is True and row.payload["policy"] == "allowed"
    assert (row.actor_kind, row.target_type) == ("user", "connector_operation") and row.actor_id


# ───────────────────────── at most once ─────────────────────────


def _is_in_doubt(raised: pytest.ExceptionInfo[ApplicationError], *, final: bool) -> None:
    assert (raised.value.type, raised.value.non_retryable) == ("EffectInDoubt", final), raised.value


async def test_a_write_whose_lease_is_lost_is_in_doubt_and_is_never_delivered_again(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    fake.lose_lease()
    async with route.serving(client, daemon, fake):
        with pytest.raises(ApplicationError) as doubt:
            await kit.play(action_id)
        _is_in_doubt(doubt, final=True)
        assert SLUG in str(doubt.value) and "never asked again" in str(doubt.value)
        # Whatever comes back to the effect (a task Temporal delivers twice) meets the same answer.
        with pytest.raises(ApplicationError) as again:
            await kit.play(action_id)
        _is_in_doubt(again, final=True)
    assert served.calls == [("create_issue", TITLE)], "the server received exactly one call"
    (delivery,) = await kit.deliveries()
    assert (delivery.state, delivery.write, delivery.action_id) == ("in_doubt", True, action_id)
    (effect,) = await kit.journal(action_id)
    assert (effect.status, effect.attempts) == ("in_doubt", 1)
    # What a person is shown: where it went, who claimed it and when, the digest of what it carried.
    (sent,) = effect.result["in_doubt"]
    assert sent == {
        "outpost": SLUG,
        "endpoint": ALIAS,
        "operation": "create_issue",
        "call_id": delivery.id,
        "instance_id": daemon.instance_id,
        "claimed_at": sent["claimed_at"],
        "arguments_sha256": arguments_sha256(TITLE),
    }
    assert sent["claimed_at"] and effect.result["again"] is False
    assert "door stuck" not in str(effect.result), "the digest of the arguments, never the arguments"


async def test_an_effect_retried_after_its_claim_reads_the_first_answer_and_places_nothing(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The worker dies once the outpost answered and before the answer is recorded. Temporal retries
    the effect: it meets the first call and reads the stored outcome. Once its result is recorded,
    and only then, what the rendezvous held of the answer is emptied."""
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    async with route.serving(client, daemon, fake):
        assert await kit.attempt_that_dies(action_id) == {"key": "OPS-7"}
    (delivery,) = await kit.deliveries()
    assert delivery.state == "answered" and delivery.answer_sealed is not None, (
        "the answer waits for whoever makes the effect's result durable"
    )
    # The outpost has gone away since: the retry needs nobody there, it places nothing.
    done = await kit.play(action_id)
    assert (done["replayed"], done["result"]) == (False, {"key": "OPS-7"})
    assert served.calls == [("create_issue", TITLE)], "one delivery, whatever was retried"
    (delivery,) = await kit.deliveries()
    assert delivery.state == "answered"
    assert delivery.answer_sealed is None and delivery.answer_key_ciphertext is None, (
        "emptied in the transaction that recorded the effect's result"
    )
    (effect,) = await kit.journal(action_id)
    assert (effect.status, effect.result) == ("done", {"key": "OPS-7"})
    # A third time: the effect's own record answers, and nothing is asked of the rendezvous.
    assert (await kit.play(action_id))["replayed"] is True
    assert len(await kit.deliveries()) == 1


async def _until(reached: Any, what: str) -> None:
    for _ in range(200):
        if await reached():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"never happened: {what}")


async def test_two_attempts_of_one_effect_in_flight_deliver_it_once(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """An attempt Temporal gave up on may still be waiting when the next begins. Both place under
    the same id: the table holds the call once, an instance claims it once, and both read the one
    answer."""
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])

    async def placed() -> bool:
        return len(await kit.deliveries()) == 1

    async def both_began() -> bool:
        return [effect.attempts for effect in await kit.journal(action_id)] == [2]

    fake.go_offline()  # nobody claims yet: the first attempt's call waits
    async with route.serving(client, daemon, fake):
        first = asyncio.create_task(kit.play(action_id))
        await _until(placed, "the first attempt placed its call")
        second = asyncio.create_task(kit.play(action_id))
        await _until(both_began, "the second attempt began while the first waited")
        fake.come_back()
        one, other = await asyncio.gather(first, second)
    assert one["result"] == other["result"] == {"key": "OPS-7"}
    assert served.calls == [("create_issue", TITLE)], "claimed once, made once"
    (delivery,) = await kit.deliveries()
    assert delivery.state == "answered"
    (effect,) = await kit.journal(action_id)
    assert (effect.status, effect.attempts) == ("done", 2)


async def test_a_retry_after_the_answer_was_swept_is_in_doubt_and_never_a_second_call(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The retry comes after the sweep emptied the answer nobody recorded (fifteen minutes). The
    write was delivered; what it returned is gone. It is not delivered again to find out: the effect
    ends in doubt, and a person looks."""
    from choregos_api.outposts import rendezvous
    from choregos_core import utcnow

    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    async with route.serving(client, daemon, fake):
        await kit.attempt_that_dies(action_id)
        await rendezvous.sweep(now=utcnow() + timedelta(seconds=rendezvous.ANSWER_KEPT_S + 60))
        (delivery,) = await kit.deliveries()
        assert delivery.state == "answered" and delivery.answer_sealed is None
        with pytest.raises(ApplicationError) as doubt:
            await kit.play(action_id)
        _is_in_doubt(doubt, final=True)
        assert "answered the write" in str(doubt.value) and "no longer holds its answer" in str(doubt.value)
    assert served.calls == [("create_issue", TITLE)], "a delivered write is not turned into a second call"
    assert len(await kit.deliveries()) == 1
    (effect,) = await kit.journal(action_id)
    assert effect.status == "in_doubt" and effect.result["in_doubt"][0]["call_id"] == delivery.id


# ───────────────────────── what is retried ─────────────────────────


async def test_a_write_nobody_claimed_is_retried_and_succeeds_once_the_outpost_is_back(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """It reached no server (ADR 0149 §5): the failure is one Temporal retries, not a refusal and not
    a doubt, and the retry delivers the write for the first time, under the same id."""
    from choregos_adapters.outpost import OutpostOffline

    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    fake.go_offline()
    async with route.serving(client, daemon, fake):
        with pytest.raises(OutpostOffline):
            await kit.play(action_id)
        (delivery,) = await kit.deliveries()
        assert (delivery.state, delivery.claimed_at) == ("expired", None)
        (effect,) = await kit.journal(action_id)
        assert (effect.status, effect.attempts) == ("started", 1) and "offline" in effect.error
        assert served.calls == []
        fake.come_back()
        assert (await kit.play(action_id))["result"] == {"key": "OPS-7"}
    assert served.calls == [("create_issue", TITLE)]
    (again,) = await kit.deliveries()
    assert (again.id, again.state) == (delivery.id, "answered"), "the same call, placed again"
    (effect,) = await kit.journal(action_id)
    assert (effect.status, effect.attempts) == ("done", 2)


async def test_a_quota_is_retried_for_a_write_and_the_outposts_own_refusal_is_final(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.outpost import OutpostNotAdmitted

    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    # A quota of the door passes by itself: nothing was written, the effect's policy retries.
    busy = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    outposts_kit.configure(monkeypatch, outposts_calls_per_minute_per_outpost="1")
    with pytest.raises(OutpostNotAdmitted) as quota:
        await kit.play(busy)
    assert quota.value.reason == "outpost_rate"
    outposts_kit.configure(monkeypatch, outposts_calls_per_minute_per_outpost="600")
    # What the outpost's own policy refuses will be refused again: final, and nothing reached its server.
    refused = await kit.action(project["id"], [kit.call("create_issue", {"title": "another"})])
    fake.refuse_next("write_denied")
    async with route.serving(client, daemon, fake):
        with pytest.raises(ApplicationError) as final:
            await kit.play(refused)
    assert (final.value.type, final.value.non_retryable) == ("EffectRefused", True)
    assert "write_denied" in str(final.value) and served.calls == []


async def test_a_read_played_by_an_effect_still_fails_fast_when_nobody_claims_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Only a write nobody claimed is left to the effect's retries: an approved read is a refusal
    that says why, as it was."""
    _served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake)
    action_id = await kit.action(project["id"], [kit.call("get_issue", {})])
    fake.go_offline()
    async with route.serving(client, daemon, fake):
        with pytest.raises(ApplicationError) as final:
            await kit.play(action_id)
    assert (final.value.type, final.value.non_retryable) == ("EffectRefused", True)
    assert "offline" in str(final.value)


async def test_an_operation_that_takes_the_key_is_delivered_again_with_the_same_key(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The one exception (ADR 0149 §5): the rule lists `action_key`, the platform fills it, and after
    a doubt the write is delivered again with the same key. The server, not the platform, makes the
    second delivery harmless. Each delivery is its own call of the rendezvous: a claimed write is
    never handed over twice under one id."""
    served, fake = kit.outpost()
    both = {"platform_arguments": ["action_key", "work_item_key"]}
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_ticket": both})
    written = TITLE | {"action_key": "what-the-agent-wrote"}
    action_id = await kit.action(project["id"], [kit.call("create_ticket", written)])
    fake.lose_lease()
    async with route.serving(client, daemon, fake):
        with pytest.raises(ApplicationError) as doubt:
            await kit.play(action_id)
        _is_in_doubt(doubt, final=False)
        (effect,) = await kit.journal(action_id)
        assert (effect.status, effect.result["again"]) == ("in_doubt", True)
        # The retry of the effect's own policy: delivered again.
        assert (await kit.play(action_id))["result"] == {"ticket": "T-1"}
    first, second = served.calls
    assert first == second, "the same call, twice"
    key = first[1]["action_key"]
    assert first[0] == "create_ticket" and len(key) == 24 and key != "what-the-agent-wrote"
    assert first[1] == TITLE | {"action_key": key, "work_item_key": "varga/x#1"}, (
        "what the rule lists, filled"
    )
    lost, answered = await kit.deliveries()
    assert (lost.state, answered.state) == ("in_doubt", "answered") and lost.id != answered.id
    (effect,) = await kit.journal(action_id)
    assert (effect.status, effect.attempts, effect.result) == ("done", 2, {"ticket": "T-1"})


async def test_an_operation_that_only_accepts_a_doubt_is_not_delivered_again(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The same tool, opened under the other statement: a doubt is final, whatever the tool takes."""
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_ticket": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_ticket", TITLE)])
    fake.lose_lease()
    async with route.serving(client, daemon, fake):
        for _attempt in range(2):
            with pytest.raises(ApplicationError) as doubt:
                await kit.play(action_id)
            _is_in_doubt(doubt, final=True)
    assert served.calls == [("create_ticket", TITLE)], "no key was filled, and nothing was sent twice"


async def test_a_rule_that_no_longer_lists_the_key_delivers_nothing_again(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A doubt the effect's policy would retry, and before the retry the administrator swaps the
    statement: the tool no longer takes the key. The retry is no second delivery: it meets the first
    call, and the doubt is final."""
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_ticket": KEYED})
    action_id = await kit.action(project["id"], [kit.call("create_ticket", TITLE)])
    fake.lose_lease()
    async with route.serving(client, daemon, fake):
        with pytest.raises(ApplicationError) as doubt:
            await kit.play(action_id)
        _is_in_doubt(doubt, final=False)
        swapped = await kit.state(client, "create_ticket", in_doubt_accepted=True, platform_arguments=[])
        assert swapped.status_code == 200, swapped.text
        with pytest.raises(ApplicationError) as final:
            await kit.play(action_id)
        _is_in_doubt(final, final=True)
    assert [name for name, _ in served.calls] == ["create_ticket"], "delivered once"
    assert len(await kit.deliveries()) == 1


# ───────────────────────── a worker that stops, an outpost that is paused ─────────────────────────


async def _claimed_then_its_worker_stops(
    client: AsyncClient, daemon: Any, fake: Any, action_id: str
) -> dict[str, Any]:
    """An attempt places its write, an instance of the outpost claims it, and the worker that waited
    stops (its task is cancelled, as at a shutdown). Returns the call as the instance read it."""

    async def placed() -> bool:
        return len(await kit.deliveries()) == 1

    attempt = asyncio.create_task(kit.play(action_id))
    await _until(placed, "the attempt placed its write")
    (claimed,) = await daemon.calls(client, endpoints=fake.aliases, capacity=8)
    attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await attempt
    return dict(claimed)


async def test_a_worker_that_stops_while_its_write_is_claimed_does_not_put_it_in_doubt(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The write is not given up when whoever waited for it goes away: the instance still answers
    it, and the effect played again by another worker meets the same call and reads that answer."""
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    claimed = await _claimed_then_its_worker_stops(client, daemon, fake, action_id)
    (delivery,) = await kit.deliveries()
    assert delivery.state == "claimed", "still leased to the instance that took it"
    made = await fake.make(claimed)
    assert made.answer is not None
    assert (await daemon.answer(client, claimed, **made.answer)).status_code == 200, "the answer is taken"
    assert (await kit.play(action_id))["result"] == {"key": "OPS-7"}
    assert served.calls == [("create_issue", TITLE)]
    (effect,) = await kit.journal(action_id)
    assert (effect.status, effect.attempts) == ("done", 2)


async def test_a_retry_refused_after_its_write_was_claimed_is_in_doubt_not_a_failure(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A worker stops while its write is claimed, and before the effect is played again an
    administrator pauses the outpost. The retry is refused before it leaves. Read as a failure it
    would compensate what the action had done around a write that may have been made: the effect
    ends in doubt, with the call that was claimed."""
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    await _claimed_then_its_worker_stops(client, daemon, fake, action_id)
    (delivery,) = await kit.deliveries()
    assert (await client.post(f"{ORG}/outposts/{SLUG}/pause")).status_code == 200
    with pytest.raises(ApplicationError) as doubt:
        await kit.play(action_id)
    _is_in_doubt(doubt, final=True)
    assert "is paused" in str(doubt.value) and "was claimed" in str(doubt.value)
    (effect,) = await kit.journal(action_id)
    assert effect.status == "in_doubt"
    (sent,) = effect.result["in_doubt"]
    assert sent == {
        "outpost": SLUG,
        "endpoint": ALIAS,
        "operation": "create_issue",
        "call_id": delivery.id,
        "instance_id": daemon.instance_id,
        "claimed_at": sent["claimed_at"],
        "arguments_sha256": arguments_sha256(TITLE),
    }
    assert sent["claimed_at"] and served.calls == []


async def test_a_retry_refused_after_a_write_nobody_claimed_is_a_refusal(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The same pause, after a write that no instance took: it reached no server, and the refusal
    is what it says."""
    from choregos_adapters.outpost import OutpostOffline

    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    fake.go_offline()
    async with route.serving(client, daemon, fake):
        with pytest.raises(OutpostOffline):
            await kit.play(action_id)
    assert (await client.post(f"{ORG}/outposts/{SLUG}/pause")).status_code == 200
    with pytest.raises(ApplicationError) as refused:
        await kit.play(action_id)
    assert (refused.value.type, refused.value.non_retryable) == ("EffectRefused", True)
    assert "is paused" in str(refused.value) and served.calls == []
    (effect,) = await kit.journal(action_id)
    assert effect.status == "started"


# ───────────────────────── compensation ─────────────────────────

UNDO = {"operation": "delete_issue", "arguments": {"key": "{{ result.key }}"}}


async def test_a_compensation_through_the_same_outpost_is_a_call_of_its_own(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A compensation reuses its effect's key. Through the connector its effect went through it must
    not meet its effect's call: it is another write, placed once under an id of its own."""
    from choregos_api.services.outpost_route import write_call_id
    from choregos_orchestrator.activities.actions import compensate_effect

    served, fake = kit.outpost()
    opened = {"create_issue": ACCEPTED, "delete_issue": ACCEPTED}
    daemon, _headers = await kit.prepared(client, project, fake, opened=opened)
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE, compensate=UNDO)])
    async with route.serving(client, daemon, fake):
        await kit.play(action_id)
        undone = await compensate_effect({"action_id": action_id, "position": 0})
        assert undone == {"key": f"{action_id}:0", "undone": True}, undone
        # Asked again (a task delivered twice): the effect is no longer `done`, nothing is undone twice.
        assert (await compensate_effect({"action_id": action_id, "position": 0}))["undone"] is False
    assert served.calls == [("create_issue", TITLE), ("delete_issue", {"key": "OPS-7"})]
    written, removed = await kit.deliveries()
    connector = await route.connector_row()
    assert written.id == write_call_id(connector, f"{action_id}:0")
    assert removed.id == write_call_id(connector, f"{action_id}:0:compensation") != written.id
    assert (written.tool, removed.tool) == ("create_issue", "delete_issue")
    assert written.answer_sealed is None and removed.answer_sealed is None, "both emptied once recorded"
    (effect,) = await kit.journal(action_id)
    assert effect.status == "compensated"


async def test_a_compensation_whose_lease_is_lost_says_so_and_is_not_played_again(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_orchestrator.activities.actions import compensate_effect

    served, fake = kit.outpost()
    opened = {"create_issue": ACCEPTED, "delete_issue": ACCEPTED}
    daemon, _headers = await kit.prepared(client, project, fake, opened=opened)
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE, compensate=UNDO)])
    async with route.serving(client, daemon, fake):
        await kit.play(action_id)
        fake.lose_lease()
        undone = await compensate_effect({"action_id": action_id, "position": 0})
    assert undone["undone"] is False and "EffectInDoubt" in undone["reason"], undone
    (effect,) = await kit.journal(action_id)
    assert effect.status == "compensation_failed", "what it could not undo, it says"
    assert served.calls == [("create_issue", TITLE), ("delete_issue", {"key": "OPS-7"})]


# ───────────────────────── a row edited behind the API ─────────────────────────


async def _behind_the_api(operation: str, **values: Any) -> None:
    from choregos_api.db.models import ConnectorOperation
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    async with session_scope(orgs="*") as session:
        await session.execute(
            update(ConnectorOperation).where(ConnectorOperation.name == operation).values(**values)
        )


async def test_a_write_opened_behind_the_api_under_neither_statement_is_delivered_by_nobody(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The rule is read again where the write would leave, as the policy is: a row that says
    `allowed` and states nothing places no call, key or not."""
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake)
    await _behind_the_api("create_issue", policy="allowed")
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    async with route.serving(client, daemon, fake):
        with pytest.raises(ApplicationError) as refused:
            await kit.play(action_id)
    assert (refused.value.type, refused.value.non_retryable) == ("EffectRefused", True)
    assert "`platform_arguments`" in str(refused.value) and "`in_doubt_accepted`" in str(refused.value)
    assert await kit.deliveries() == [] and served.calls == []


# ───────────────────────── the action, played by its workflow ─────────────────────────


@pytest.fixture
async def temporal() -> AsyncIterator[Any]:
    """A Temporal test server: the `ActionWorkflow` is the orchestrator's own, with its workers."""
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


async def _start(temporal: Any, action_id: str, workflow_id: str | None = None) -> Any:
    return await temporal.client.start_workflow(
        "ActionWorkflow", {"action_id": action_id}, id=workflow_id or f"action-{action_id}", task_queue=QUEUE
    )


async def test_the_action_of_a_write_whose_lease_is_lost_ends_in_doubt_and_nothing_replays_it(
    temporal: Any, client: AsyncClient, project: dict[str, Any], app: Any
) -> None:
    """The whole chain: the first write is answered, the lease of the second is lost. The action ends
    `in_doubt`: the server received each call once, the first write is not undone, the third effect
    never runs, and no route plays the action again."""
    from choregos_api.services.action_relay import start_approved_actions
    from temporalio.client import WorkflowFailureError

    served, fake = kit.outpost()
    opened = dict.fromkeys(("create_issue", "update_issue", "delete_issue"), ACCEPTED)
    daemon, _headers = await kit.prepared(client, project, fake, opened=opened)

    def answered_too_late(arguments: dict[str, Any]) -> dict[str, Any]:
        fake.lose_lease()  # the server executes the write, and its answer never leaves the outpost
        return {"key": "OPS-7", "changed": True}

    served.gestures["update_issue"] = answered_too_late
    changed = {"title": "door stuck, twice"}
    action_id = await kit.action(
        project["id"],
        [
            kit.call("create_issue", TITLE, compensate=UNDO),
            kit.call("update_issue", changed),
            kit.call("create_issue", {"title": "never reached"}),
        ],
    )
    async with route.serving(client, daemon, fake), _workers(temporal):
        result = await asyncio.wait_for((await _start(temporal, action_id)).result(), timeout=60)
        assert result["status"] == "in_doubt" and result["effects"] == [0], result

        # The server received exactly one call of each write that left, and no compensation.
        assert served.calls == [("create_issue", TITLE), ("update_issue", changed)]
        first, second = await kit.deliveries()
        assert (first.state, second.state) == ("answered", "in_doubt")
        done, doubted = await kit.journal(action_id)
        assert (done.status, done.attempts) == ("done", 1), "not compensated"
        assert (doubted.status, doubted.attempts) == ("in_doubt", 1), "not retried"
        (sent,) = doubted.result["in_doubt"]
        assert (sent["call_id"], sent["instance_id"], sent["operation"]) == (
            second.id,
            daemon.instance_id,
            "update_issue",
        )

        # The action says it, to whoever reads it.
        shown = (await client.get(f"/api/v1/projects/{project['id']}/actions/{action_id}")).json()
        assert shown["status"] == "in_doubt" and shown["finished_at"]
        assert [(effect["status"], effect["attempts"]) for effect in shown["journal"]] == [
            ("done", 1),
            ("in_doubt", 1),
        ]
        assert shown["result"]["in_doubt"] == {"position": 1, "sent": [sent]}
        assert shown["result"]["compensations"] == [] and SLUG in shown["error"]
        listed = await client.get(f"/api/v1/projects/{project['id']}/actions", params={"status": "in_doubt"})
        assert [row["id"] for row in listed.json()] == [action_id]

        # No route replays it. A decision finds nothing to decide.
        decision = f"/api/v1/projects/{project['id']}/actions/{action_id}/decision"
        decided = await client.post(decision, json={"decision": "approve"})
        assert decided.status_code == 409 and "in_doubt" in decided.text, decided.text
        # The relay starts what is approved and stranded: this is neither.
        assert await start_approved_actions(grace=timedelta(0)) == []
        # A workflow started for it by anything else runs nothing and leaves it as it is.
        with pytest.raises(WorkflowFailureError) as refused:
            await (await _start(temporal, action_id, f"again-{action_id}")).result()
        assert "not approved" in str(refused.value.cause.cause)
        # An effect of it played by hand reaches nothing either.
        with pytest.raises(ApplicationError) as closed:
            await kit.play(action_id, 2)
        assert "not approved" in str(closed.value)
    assert served.calls == [("create_issue", TITLE), ("update_issue", changed)], "nothing was replayed"
    assert len(await kit.deliveries()) == 2
    assert (await kit.action_row(action_id)).status == "in_doubt"


async def test_the_workflow_retries_a_write_nobody_claimed_until_the_outpost_is_back(
    temporal: Any, client: AsyncClient, project: dict[str, Any]
) -> None:
    served, fake = kit.outpost()
    daemon, _headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)])
    fake.go_offline()
    async with route.serving(client, daemon, fake), _workers(temporal):
        handle = await _start(temporal, action_id)
        for _ in range(200):
            effects = await kit.journal(action_id)
            if effects and effects[0].error:
                break
            await asyncio.sleep(0.05)
        (effect,) = await kit.journal(action_id)
        assert (effect.status, effect.attempts) == ("started", 1) and "offline" in effect.error
        fake.come_back()
        result = await asyncio.wait_for(handle.result(), timeout=60)
    assert result == {"status": "succeeded", "effects": [0]}
    assert served.calls == [("create_issue", TITLE)], "delivered once, when the outpost was back"
    (effect,) = await kit.journal(action_id)
    assert (effect.status, effect.attempts) == ("done", 2)
    assert (await kit.action_row(action_id)).status == "succeeded"


# ───────────────────────── the agent that repeats the call ─────────────────────────


async def test_an_agent_that_repeats_the_call_gets_the_same_action_in_doubt(
    temporal: Any, client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An allowed write called by a run is a governed action under a deterministic id. Its lease is
    lost: the agent is told the action is in doubt, and telling it again changes nothing."""
    from choregos_api.temporal import RealTemporal, set_temporal

    served, fake = kit.outpost()
    daemon, headers = await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    outposts_kit.configure(monkeypatch, courtier_attente_ecriture_s=str(WAIT_S))
    address = temporal.client.service_client.config.target_host
    set_temporal(RealTemporal(address, temporal.client.namespace, QUEUE))
    fake.lose_lease()
    write = f"{BASE}/{CONNECTOR}__create_issue"
    async with route.serving(client, daemon, fake), _workers(temporal):
        answers = []
        started = time.monotonic()
        for _call in range(2):
            answered = await client.post(write, headers=headers, json=TITLE)
            assert answered.status_code == 200, answered.text
            answers.append(answered.json())
        # In doubt is an outcome the broker stops waiting on, not a wait that runs out.
        assert time.monotonic() - started < WAIT_S, "neither call waited for the broker's bound"
    first, again = answers
    assert first["status_code"] == again["status_code"] == 504, first
    told = first["result"]
    assert (told["status"], told["code"]) == ("in_doubt", "action_in_doubt")
    assert "type" not in told, "it may have reached the server: it is booked like any call that left"
    assert "nobody knows whether it was executed" in told["message"] and SLUG in told["error"]
    assert again["result"] == told, "the same action, told the same"
    assert told["decision_path"] == f"/p/billing-api/actions/{told['action']}"
    assert served.calls == [("create_issue", TITLE)], "the server received exactly one call"
    assert await route.actions() == 1 and len(await kit.deliveries()) == 1
    assert (await kit.action_row(told["action"])).status == "in_doubt"
    assert len(await route.ledger()) == 2, "each call of the agent is booked; the write left once"


async def test_a_call_under_approval_that_finds_its_action_in_doubt_is_told_so(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Under `approval` the call proposes the action. Approved since, played and ended in doubt, it
    is still the action the same call finds: not a proposal any more, and never a second one."""
    from choregos_orchestrator.activities.actions import close_action

    _served, fake = kit.outpost()
    daemon, headers = await kit.prepared(
        client, project, fake, opened={"create_issue": ACCEPTED}, policy="approval"
    )
    write = f"{BASE}/{CONNECTOR}__create_issue"
    proposed = (await client.post(write, headers=headers, json=TITLE)).json()
    assert proposed["status_code"] == 202 and proposed["result"]["status"] == "pending_approval", proposed
    action_id = proposed["result"]["action"]
    await _approved(action_id)
    fake.lose_lease()
    async with route.serving(client, daemon, fake):
        with pytest.raises(ApplicationError):
            await kit.play(action_id)
    await close_action({"action_id": action_id, "status": "in_doubt", "error": "in doubt"})
    repeated = (await client.post(write, headers=headers, json=TITLE)).json()
    assert repeated["status_code"] == 504, repeated
    assert (repeated["result"]["action"], repeated["result"]["status"]) == (action_id, "in_doubt")
    assert "approves it" not in repeated["result"]["message"]
    assert await route.actions() == 1


async def _approved(action_id: str) -> None:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        row = await session.get(Action, action_id)
        assert row is not None
        row.status = "approved"
        row.decisions = [{"by": "ops@varga.dev", "decision": "approve"}]


async def test_a_conversation_follows_an_action_in_doubt_without_calling_it_done_or_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """An assistant's governed action that ended in doubt is neither executed nor refused: the row of
    the conversation keeps saying `governed`, with the status it follows."""
    from types import SimpleNamespace

    from choregos_api.db.session import session_scope
    from choregos_api.services.conversation_actions import _follow_governed

    _served, fake = kit.outpost()
    await kit.prepared(client, project, fake, opened={"create_issue": ACCEPTED})
    action_id = await kit.action(project["id"], [kit.call("create_issue", TITLE)], status="in_doubt")
    row: Any = SimpleNamespace(status="governed", result={"action": action_id}, confirmed_at=None)
    async with session_scope(orgs="*") as session:
        await _follow_governed(session, row)
    assert (row.status, row.result["action_status"]) == ("governed", "in_doubt")
