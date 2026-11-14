# SPDX-License-Identifier: Apache-2.0
"""A connector reached through an outpost is governed like a direct one (S65-13, ADR 0149).

An `mcp-outpost` connector names an outpost and an endpoint alias: no URL, no secret. Everything that
decides a call is decided before it leaves, where it is decided for a direct connector; only the
transport of the outbound segment changes, a row an instance of the outpost claims at the door
instead of an HTTP call to an MCP server. So the same call through both gives the same bounded
result, the same ledger line and the same digests; what the platform refuses writes no row; and what
the route itself can refuse is a typed refusal that names the outpost, never a crash: paused,
offline, refused by the outpost's own policy, not answered in time, in doubt, not admitted.

The outpost's side is a scripted daemon at the real door, in front of the scriptable `FakeOutpost`
(`kits/outpost_route.py`). They run on SQLite and, with `CHOREGOS_TEST_SUITE_ON_POSTGRES`, on
PostgreSQL under row-level security.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Any

import choregos_adapters
import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.outpost import FakeOutpost
from choregos_contracts import arguments_sha256, load_openapi
from httpx import AsyncClient

from .kits import outpost_daemon as daemon_kit
from .kits import outpost_route as route
from .kits import outposts as kit
from .kits.outpost_daemon import Daemon
from .kits.outpost_route import ALIAS, BASE, CONNECTOR, ORG, RUN, SLUG, TIMEOUT_S

SEARCH = f"{BASE}/{CONNECTOR}__search_issues"
ARGUMENTS = {"query": "door stuck"}
HOST = f"outpost:{SLUG}/{ALIAS}"


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    return route.switch_on(monkeypatch)


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_the_contracts(name: str, body: dict[str, Any]) -> None:
    """The body is what the API's contract describes under that name."""
    components = load_openapi()["components"]
    errors = daemon_kit.errors_against({**components["schemas"][name], "components": components}, body)
    assert not errors, f"not the contract's `{name}`: {errors}"


def _tool_calls(rows: list[Any]) -> list[Any]:
    return [row for row in rows if row.kind == "call_tool"]


async def _search(
    client: AsyncClient, headers: dict[str, str], arguments: dict[str, Any] | None = None
) -> Any:
    answer = await client.post(SEARCH, headers=headers, json=ARGUMENTS if arguments is None else arguments)
    assert answer.status_code == 200, answer.text
    return answer.json()


def _refused(answer: dict[str, Any], status: int, code: str, kind: str | None) -> dict[str, Any]:
    """A tool error that is the outpost's: its status, its code, the outpost and the alias named."""
    assert answer["status_code"] == status, answer
    body = dict(answer["result"])
    assert (body["code"], body["outpost"], body["endpoint"]) == (code, SLUG, ALIAS), body
    assert body.get("type") == kind, body
    assert SLUG in body["error"], "the refusal names the outpost"
    return body


# ───────────────────────── the same guarantees as a direct connector ─────────────────────────


async def _both_connectors(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, served: FakeMcpServer
) -> tuple[Daemon, FakeOutpost, dict[str, str]]:
    """One server, reached twice: by a direct `mcp` connector (`direct`) and at an outpost
    (`tracker`); the same two reads opened on each, and a run of an agent that names both."""
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", served)
    monkeypatch.setenv("CONNECTOR_TEST_DIRECT_KEY", served.token)
    declared = await client.post(
        f"{ORG}/connectors",
        json={
            "name": "direct",
            "type": "mcp",
            "config": {"url": "https://direct.test/mcp"},
            "secret_refs": {"token": "env:CONNECTOR_TEST_DIRECT_KEY"},
        },
    )
    assert declared.status_code == 201, declared.text
    assert (await client.post(f"{ORG}/connectors/direct/discover")).status_code == 200
    fake = route.outpost(served)
    daemon = await daemon_kit.approved(client)
    await route.seen(client, daemon, fake)
    await route.declare(client)
    async with route.serving(client, daemon, fake):
        assert (await client.post(f"{ORG}/connectors/{CONNECTOR}/discover")).status_code == 200
    for connector in ("direct", CONNECTOR):
        for operation in ("search_issues", "get_issue"):
            assert (await route.open_operation(client, operation, connector=connector)).status_code == 200
    return daemon, fake, await route.agent_and_run(client, project, ["direct", CONNECTOR])


@pytest.mark.parametrize(
    "scripted",
    [
        {"answers": {"search_issues": "three issues match"}},
        {"structured": {"search_issues": {"issues": [{"key": "OPS-1", "title": "door stuck"}]}}},
        # Larger than the platform's bound: both are cut, at the same byte, with the same marker.
        {"answers": {"search_issues": "a long page of issues. " * 6000}},
    ],
    ids=["text", "structured", "truncated"],
)
async def test_the_same_call_through_a_direct_connector_and_through_an_outpost_is_the_same(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, scripted: dict[str, Any]
) -> None:
    served = route.server(token="direct-key", **scripted)
    daemon, fake, headers = await _both_connectors(client, project, monkeypatch, served)
    served.calls.clear()

    direct = await client.post(f"{BASE}/direct__search_issues", headers=headers, json=ARGUMENTS)
    async with route.serving(client, daemon, fake):
        through = await client.post(SEARCH, headers=headers, json=ARGUMENTS)
    assert direct.status_code == through.status_code == 200, (direct.text, through.text)

    # The same bounded result, to the byte: what the agent reads does not say which way it came.
    assert through.json() == direct.json()
    assert direct.json()["status_code"] == 200
    if "truncated" in direct.json()["result"]:
        assert through.json()["result"]["truncated"] == direct.json()["result"]["truncated"]
    # The server received the same call twice, and nothing else.
    assert served.calls == [("search_issues", ARGUMENTS)] * 2

    # The same ledger line: a tool call of the run, under `mcp:<connector>`.
    def line(row: Any) -> tuple[Any, ...]:
        return (row.kind, row.run_id, row.cost_usd, row.cost_eur, row.stage_role, row.agent_slug)

    (direct_line,) = await route.ledger("mcp:direct")
    (outpost_line,) = await route.ledger(f"mcp:{CONNECTOR}")
    assert line(outpost_line) == line(direct_line) == ("tool", RUN, 0.0, 0.0, "plan", "coordinator")
    assert outpost_line.model == f"{CONNECTOR}__search_issues"

    # The same `tool.called` digests: of the arguments as sent, of the result as served.
    (direct_event,) = await route.events("direct__search_issues")
    (outpost_event,) = await route.events(f"{CONNECTOR}__search_issues")
    same = ("request_sha256", "response_sha256", "response_bytes", "status_code", "origin")
    assert {k: outpost_event.payload[k] for k in same} == {k: direct_event.payload[k] for k in same}
    assert outpost_event.payload["request_sha256"] == _digest(ARGUMENTS)
    assert outpost_event.payload["response_sha256"] == _digest(through.json()["result"])
    assert outpost_event.payload["provider"] == f"mcp:{CONNECTOR}"


async def test_the_outpost_is_told_who_the_call_is_made_for_and_nothing_it_could_decide_on(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    fake.received.clear()
    async with route.serving(client, daemon, fake):
        assert (await _search(client, headers))["status_code"] == 200
    (call,) = fake.received
    assert (call["kind"], call["endpoint"], call["tool"]) == ("call_tool", ALIAS, "search_issues")
    assert call["arguments"] == ARGUMENTS
    # The names the gateway route signs into an assertion, minus the server and the tool (ADR 0149 §3).
    assert call["asserted"] == {
        "sub": f"run:{RUN}",
        "org": "varga",
        "org_id": (await kit.outpost_row(SLUG)).org_id,
        "purpose": "tool_call",
        "project": "billing-api",
        "run": RUN,
        "work_item": "varga/x#1",
        "agent": "coordinator@1",
        "initiator": "person:admin@varga.dev",
        "connector": CONNECTOR,
    }
    # Whether the tool writes is the outpost's to read from its own server (ADR 0147 §6).
    assert "write" not in call and "access" not in call


async def test_what_the_platform_refuses_writes_no_rendezvous_row(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A forbidden operation, an argument outside its schema, an argument outside its grant and an
    operation under approval: each is decided before anything leaves, and leaves nothing to claim."""
    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    fake.received.clear()
    bound = {"properties": {"query": {"const": "door stuck"}}, "required": ["query"]}
    ruled = await client.patch(
        f"{ORG}/connectors/{CONNECTOR}/operations/search_issues", json={"arguments": bound}
    )
    assert ruled.status_code == 200, ruled.text
    assert (await route.open_operation(client, "get_issue", "approval")).status_code == 200

    async with route.serving(client, daemon, fake):
        # Forbidden: `create_issue` was discovered and never opened. It does not exist for the run.
        unknown = await client.post(f"{BASE}/{CONNECTOR}__create_issue", headers=headers, json={})
        assert unknown.status_code == 404, unknown.text
        # Outside the operation's own schema.
        malformed = await client.post(SEARCH, headers=headers, json={})
        assert malformed.status_code == 400, malformed.text
        # Inside the schema, outside what the organisation grants.
        outside = await client.post(SEARCH, headers=headers, json={"query": "everything"})
        assert outside.status_code == 400 and outside.json()["code"] == "arguments_outside_grant"
        # Under approval: the call proposes an action a person decides; nothing is sent.
        proposed = await client.post(f"{BASE}/{CONNECTOR}__get_issue", headers=headers, json={})
        assert proposed.json()["status_code"] == 202, proposed.text
        assert proposed.json()["result"]["status"] == "pending_approval"
        assert _tool_calls(await route.calls()) == [], "nothing was placed for an outpost to claim"
        assert fake.received == [], "and the outpost was handed nothing"
        # The same route, with what the grant allows: one row, one call.
        assert (await _search(client, headers))["status_code"] == 200
    assert len(_tool_calls(await route.calls())) == 1
    assert [call["tool"] for call in fake.received] == ["search_issues"]


async def test_the_calls_record_holds_digests_written_when_the_answer_is_opened(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """`result_sha256` is written by the side that placed the call, when it opens the answer: the door
    stored the envelope sealed and could not (S65-10b). What waited on the row is gone once read."""
    served = route.server(structured={"search_issues": {"issues": [{"key": "OPS-1"}]}})
    fake = route.outpost(served)
    daemon, headers = await route.prepared(client, project, fake)
    async with route.serving(client, daemon, fake):
        assert (await _search(client, headers))["status_code"] == 200
    raw = {
        "content": [{"type": "text", "text": "search_issues done"}],
        "isError": False,
        "structuredContent": {"issues": [{"key": "OPS-1"}]},
    }
    listed = await client.get(f"{ORG}/outposts/{SLUG}/calls")
    assert listed.status_code == 200, listed.text
    (record,) = [item for item in listed.json()["items"] if item["kind"] == "call_tool"]
    assert (record["state"], record["outcome"], record["is_error"]) == ("answered", "result", False)
    assert record["result_sha256"] == _digest(raw), "the digest of what the outpost's server answered"
    assert record["arguments_sha256"] == arguments_sha256(ARGUMENTS)
    assert (record["connector"], record["project"], record["run_id"]) == (CONNECTOR, "billing-api", RUN)
    assert (record["endpoint"], record["tool"], record["write"]) == (ALIAS, "search_issues", False)
    (row,) = _tool_calls(await route.calls())
    assert row.answer_sealed is None and row.answer_key_ciphertext is None, "the answer was forgotten"
    assert row.arguments_ciphertext is None and row.asserted is None


# ───────────────────────── the refusals of the route ─────────────────────────


async def test_an_outpost_nobody_polls_is_a_refusal_at_once_that_writes_and_books_nothing(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import OutpostInstance

    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    placed = len(await route.calls())
    # Its one instance was last seen long ago: nobody is there to claim anything.
    long_ago = datetime(2026, 1, 1, tzinfo=UTC)
    await daemon_kit.change(OutpostInstance, str(daemon.instance_id), last_seen_at=long_ago)
    started = time.monotonic()
    answer = await _search(client, headers)
    assert time.monotonic() - started < TIMEOUT_S, "it did not wait for a claim that could not come"
    _refused(answer, 503, "outpost_offline", "network_policy")
    assert len(await route.calls()) == placed, "nothing was written"
    assert await route.ledger() == [], "a call that reached no server is not booked"
    assert (HOST, "broker", "unreachable", "outpost_offline", 1) in await route.journal()


async def test_a_call_nobody_claims_in_time_is_offline_within_the_connectors_timeout(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    fake = route.outpost()
    _daemon, headers = await route.prepared(client, project, fake, timeout_s=TIMEOUT_S)
    # The instance polled a moment ago, and polls no more: the call is placed, and waits.
    started = time.monotonic()
    answer = await _search(client, headers)
    waited = time.monotonic() - started
    _refused(answer, 503, "outpost_offline", "network_policy")
    assert TIMEOUT_S - 0.5 <= waited < TIMEOUT_S + 2, f"waited {waited:.1f}s for a {TIMEOUT_S}s connector"
    (row,) = _tool_calls(await route.calls())
    assert (row.state, row.instance_id, row.arguments_ciphertext) == ("expired", None, None)
    assert await route.ledger() == []
    assert (HOST, "broker", "unreachable", "outpost_offline", 1) in await route.journal()
    assert fake.received == [call for call in fake.received if call["kind"] == "list_tools"]


async def test_an_alias_no_instance_serves_is_offline_for_that_alias(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    placed = len(await route.calls())
    # The instance is there, and its policy file no longer has the alias: it announces another.
    polled = await daemon.poll(client, endpoints=["forge"])
    assert polled.status_code == 200, polled.text
    body = _refused(await _search(client, headers), 503, "outpost_offline", "network_policy")
    assert f"`{ALIAS}`" in body["error"]
    assert len(await route.calls()) == placed


@pytest.mark.parametrize("gesture", ["pause", "revoke", "switch_off"])
async def test_an_outpost_that_is_handed_nothing_refuses_before_anything_is_written(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, gesture: str
) -> None:
    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    placed = len(await route.calls())
    if gesture == "switch_off":
        kit.configure(monkeypatch, outposts=None)
    else:
        assert (await client.post(f"{ORG}/outposts/{SLUG}/{gesture}")).status_code == 200
    async with route.serving(client, daemon, fake) if gesture == "pause" else contextlib.nullcontext():
        _refused(await _search(client, headers), 403, "outpost_paused", "network_policy")
        # An approval is not even proposed: the route is judged before the action exists.
        assert (await route.open_operation(client, "get_issue", "approval")).status_code == 200
        gated = await client.post(f"{BASE}/{CONNECTOR}__get_issue", headers=headers, json={})
        assert gated.status_code == 403, gated.text
        assert (gated.json()["code"], gated.json()["outpost"]) == ("outpost_paused", SLUG)
    assert len(await route.calls()) == placed
    assert await route.ledger() == [] and await route.actions() == 0
    if gesture == "pause":
        assert (await client.post(f"{ORG}/outposts/{SLUG}/resume")).status_code == 200
        async with route.serving(client, daemon, fake):
            assert (await _search(client, headers))["status_code"] == 200


@pytest.mark.parametrize("code", ["tool_not_allowed", "unknown_endpoint", "write_denied", "busy"])
async def test_a_refusal_of_the_outposts_own_policy_is_a_422_that_carries_its_code(
    client: AsyncClient, project: dict[str, Any], code: str
) -> None:
    served = route.server()
    fake = route.outpost(served)
    daemon, headers = await route.prepared(client, project, fake)
    fake.refuse_next(code)
    async with route.serving(client, daemon, fake):
        body = _refused(await _search(client, headers), 422, "outpost_refused", "network_policy")
    assert body["refusal"] == code
    assert served.calls == [], "the outpost's server received nothing"
    (row,) = _tool_calls(await route.calls())
    assert (row.state, row.refusal) == ("refused", code)
    assert await route.ledger() == [], "a call that reached no server is not booked"
    assert (HOST, "broker", "denied", f"outpost_refused:{code}", 1) in await route.journal()


async def test_a_call_the_door_cannot_carry_is_refused_with_the_doors_own_code(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A call that passed admission and weighs too much once sealed for its claimer is not handed
    over: the door refuses it with the code an outpost gives a payload above its ceiling."""
    from choregos_api.outposts import rendezvous

    served = route.server()
    fake = route.outpost(served)
    daemon, headers = await route.prepared(client, project, fake)
    monkeypatch.setattr(rendezvous, "_would_weigh", lambda *_: 0)  # admission sees nothing wrong
    monkeypatch.setattr(rendezvous, "MAX_SEALED_CALL_BYTES", 600)
    async with route.serving(client, daemon, fake):
        body = _refused(await _search(client, headers), 422, "outpost_refused", "network_policy")
    assert body["refusal"] == "payload_too_large"
    assert served.calls == [] and await route.ledger() == []


async def test_a_call_too_large_to_cross_the_door_is_refused_where_it_is_admitted(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The contract's bound (`max_sealed_call_bytes`, 2 MiB): refused before anything is written."""
    from choregos_contracts.outpost import MAX_SEALED_CALL_BYTES

    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    placed = len(await route.calls())
    heavy = {"query": "x" * (MAX_SEALED_CALL_BYTES - 1000)}
    async with route.serving(client, daemon, fake):
        body = _refused(await _search(client, headers, heavy), 413, "outpost_not_admitted", "network_policy")
        assert body["reason"] == "too_large"
        assert len(await route.calls()) == placed, "nothing was written"
        # Under the bound, and under the outpost's own ceiling, the same call is placed and answered.
        light = {"query": "x" * 200_000}
        assert (await _search(client, headers, light))["status_code"] == 200
    assert len(await route.ledger()) == 1, "the call that was refused is not booked"


async def test_a_claimed_read_that_gets_no_answer_times_out_and_is_booked(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    served = route.server()
    fake = route.outpost(served)
    daemon, headers = await route.prepared(client, project, fake, timeout_s=TIMEOUT_S)
    fake.lose_lease()
    async with route.serving(client, daemon, fake):
        started = time.monotonic()
        body = _refused(await _search(client, headers), 504, "outpost_timeout", None)
        assert time.monotonic() - started < TIMEOUT_S + 2
    assert "may be asked again" in body["error"]
    (row,) = _tool_calls(await route.calls())
    assert row.state == "timed_out" and row.instance_id == daemon.instance_id
    # Its server did receive the call: executed or not, nobody knows, so it is booked like a call that left.
    assert served.calls == [("search_issues", ARGUMENTS)]
    assert len(await route.ledger()) == 1
    assert (HOST, "broker", "unreachable", "outpost_timeout", 1) in await route.journal()
    # A read may be asked again: a new call, a new row, an answer.
    await route.set_timeout(client, route.SETUP_TIMEOUT_S)
    async with route.serving(client, daemon, fake):
        assert (await _search(client, headers))["status_code"] == 200
    assert [row.state for row in _tool_calls(await route.calls())] == ["timed_out", "answered"]


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        ({"outposts_calls_in_flight_per_outpost": "1"}, "outpost_quota"),
        ({"outposts_calls_in_flight_per_organisation": "1"}, "organisation_quota"),
        ({"outposts_calls_per_minute_per_outpost": "2"}, "outpost_rate"),
        ({"outposts_calls_per_minute_per_organisation": "2"}, "organisation_rate"),
    ],
)
async def test_a_call_beyond_a_quota_is_not_admitted_and_says_which(
    client: AsyncClient,
    project: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    settings: dict[str, str],
    reason: str,
) -> None:
    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    kit.configure(monkeypatch, **settings)
    if reason.endswith("_quota"):
        # One call in flight already: placed, and claimed by nobody yet.
        await daemon_kit.place()
    else:
        # The discovery was the minute's first call; this one is its second, and last.
        async with route.serving(client, daemon, fake):
            assert (await _search(client, headers))["status_code"] == 200
    placed, booked = len(await route.calls()), len(await route.ledger())
    async with route.serving(client, daemon, fake) if reason.endswith("_rate") else contextlib.nullcontext():
        body = _refused(await _search(client, headers), 429, "outpost_not_admitted", "rate_limited")
    assert body["reason"] == reason
    assert len(await route.calls()) == placed, "nothing was written"
    assert len(await route.ledger()) == booked, "and nothing was booked"
    assert (HOST, "broker", "rate_limited", f"outpost_not_admitted:{reason}", 1) in await route.journal()


async def test_a_deployment_without_a_secrets_key_hands_nothing_to_an_outpost(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outposts on, fakes off, and no key to seal a call's arguments under while they wait: a typed
    refusal that says so, not the store's exception."""
    from choregos_api.secret_store import ENV_KEKS

    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    placed = len(await route.calls())
    monkeypatch.delenv(ENV_KEKS)
    monkeypatch.delenv("CHOREGOS_FAKES")
    async with route.serving(client, daemon, fake):
        body = _refused(await _search(client, headers), 403, "outpost_paused", "network_policy")
    assert "CHOREGOS_SECRETS_KEKS" in body["error"]
    assert len(await route.calls()) == placed and await route.ledger() == []


async def test_a_caller_that_goes_away_cancels_its_call(client: AsyncClient, project: dict[str, Any]) -> None:
    """Whoever waited gave up: the platform says it stops waiting, and the instance that holds the
    call learns it at its next poll. The read it held is closed, not left to its lease."""
    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    calling = asyncio.create_task(client.post(SEARCH, headers=headers, json=ARGUMENTS))
    held: list[dict[str, Any]] = []
    for _ in range(200):
        held = await daemon.calls(client, endpoints=fake.aliases)
        if held:
            break
        await asyncio.sleep(0.01)
    assert [call["tool"] for call in held] == ["search_issues"], "the instance claimed the call"
    calling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await calling
    # The cancellation is a task of its own: the request that carried the call is gone.
    for _ in range(200):
        (row,) = _tool_calls(await route.calls())
        if row.state != "claimed":
            break
        await asyncio.sleep(0.01)
    assert row.state == "timed_out", "a claimed read nobody waits for is closed"
    polled = await daemon.poll(client, endpoints=fake.aliases, running=[held[0]["id"]])
    assert polled.json()["cancel"] == [held[0]["id"]]


async def test_a_call_the_door_does_not_carry_is_a_refusal_not_a_crash(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A row edited behind the API (a timeout beyond the longest lease): the rendezvous says the
    call is not one it takes, and the caller reads a refusal, never a 500."""
    from choregos_api.db.models import OrgConnector
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    placed = len(await route.calls())
    forged = {"outpost": SLUG, "endpoint": ALIAS, "timeout_s": 3600}
    async with session_scope(orgs="*") as session:
        await session.execute(
            update(OrgConnector).where(OrgConnector.name == CONNECTOR).values(config=forged)
        )
    async with route.serving(client, daemon, fake):
        body = _refused(await _search(client, headers), 422, "outpost_not_admitted", "network_policy")
    assert body["reason"] == "invalid_call" and "300 seconds" in body["error"]
    assert len(await route.calls()) == placed and await route.ledger() == []
    assert (HOST, "broker", "denied", "outpost_not_admitted:invalid_call", 1) in await route.journal()


@pytest.mark.parametrize("forged", ["none", "another organisation's"])
async def test_a_connector_that_references_no_outpost_of_its_organisation_is_handed_nothing(
    client: AsyncClient, project: dict[str, Any], forged: str
) -> None:
    """A row written behind the API, without the reference a declaration writes, or with one to an
    outpost that is not its organisation's: fail closed, under whatever scope the caller has."""
    from choregos_api.db.models import Organization, OrgConnector, Outpost
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    placed = len(await route.calls())
    async with session_scope(orgs="*") as session:
        reference = None
        if forged != "none":
            other = Organization(slug="acme", name="Acme")
            session.add(other)
            await session.flush()
            theirs = Outpost(org_id=other.id, slug=SLUG, display_name="theirs", capabilities=["tools"])
            session.add(theirs)
            await session.flush()
            reference = theirs.id
        await session.execute(
            update(OrgConnector).where(OrgConnector.name == CONNECTOR).values(outpost_id=reference)
        )
    async with route.serving(client, daemon, fake):
        body = _refused(await _search(client, headers), 403, "outpost_paused", "network_policy")
    assert "does not have" in body["error"]
    assert len(await route.calls()) == placed and await route.ledger() == []


def test_a_refusal_that_reached_no_server_carries_a_type_no_booking_counts() -> None:
    """What decides that a call is booked is its `type`: every booking of a tool call (a run's, a
    session's, a conversation's) skips `network_policy` and `rate_limited`. A refusal says whether
    the outpost's server may have received the call, and its tool error follows."""
    from choregos_adapters.outpost import (
        OutpostInDoubt,
        OutpostNotAdmitted,
        OutpostOffline,
        OutpostPaused,
        OutpostRefused,
        OutpostTimeout,
    )
    from choregos_api.services.mcp_gateway import tool_error
    from choregos_api.services.outpost_route import NEVER_LEFT
    from choregos_contracts import OutpostRefusalCode

    where = {"outpost": SLUG, "endpoint": ALIAS}
    refusals = [
        (OutpostPaused("x", **where), 403),
        (OutpostOffline("x", **where), 503),
        (OutpostRefused("x", refusal=OutpostRefusalCode.BUSY, **where), 422),
        (OutpostNotAdmitted("x", reason="outpost_quota", **where), 429),
        (OutpostNotAdmitted("x", reason="organisation_rate", **where), 429),
        (OutpostNotAdmitted("x", reason="too_large", **where), 413),
        (OutpostNotAdmitted("x", reason="write_without_key", **where), 422),
        (OutpostTimeout("x", **where), 504),
        (OutpostInDoubt("x", call_id="c", **where), 504),
    ]
    for refusal, expected in refusals:
        status, body = tool_error(refusal)
        assert status == expected, refusal
        assert (body["code"], body["outpost"], body["endpoint"]) == (refusal.code, SLUG, ALIAS)
        assert (body.get("type") in NEVER_LEFT) is refusal.reached_no_server, refusal
    assert {type(refusal).reached_no_server for refusal, _ in refusals[:7]} == {True}
    assert {"network_policy", "rate_limited"} == NEVER_LEFT


def test_the_asserted_names_are_cut_to_what_the_door_carries() -> None:
    """A name is what an outpost logs. One too long to log, or a seventeenth approver, is no reason
    to refuse the call: the contract's bounds are applied before the call is placed."""
    from choregos_api.services.mcp_gateway_identity import CallIdentity
    from choregos_api.services.outpost_route import asserted_names
    from choregos_contracts.outpost import OutpostAssertedNames

    identity = CallIdentity(
        org="varga",
        org_id="o-1",
        subject="action:" + "a" * 400,
        purpose="action",
        server="tracker",
        tool="search_issues",
        connector=CONNECTOR,
        approved_by=tuple(f"person-{n}@example.com" for n in range(20)),
    )
    names = asserted_names(identity)
    assert "server" not in names and "tool" not in names, "the outpost has the alias and the call"
    assert len(names["sub"]) == 256 and len(names["approved_by"]) == 16
    assert names["connector"] == CONNECTOR and names["org_id"] == "o-1"
    OutpostAssertedNames.model_validate(names)  # what the rendezvous checks before it writes


# ───────────────────────── discovery ─────────────────────────


async def test_discovery_through_an_outpost_adds_tools_born_forbidden(
    client: AsyncClient, admin: str
) -> None:
    served = route.server()
    fake = route.outpost(served)
    # The outpost's policy lets two patterns through: the third tool is not even listed.
    fake.tools[ALIAS] = ["search_*", "create_*"]
    daemon = await daemon_kit.approved(client)
    await route.seen(client, daemon, fake)
    await route.declare(client)
    async with route.serving(client, daemon, fake):
        found = await client.post(f"{ORG}/connectors/{CONNECTOR}/discover")
    assert found.status_code == 200, found.text
    assert sorted(found.json()["added"]) == ["create_issue", "search_issues"]
    operations = {
        op["name"]: op for op in (await client.get(f"{ORG}/connectors/{CONNECTOR}")).json()["operations"]
    }
    assert {name: op["policy"] for name, op in operations.items()} == {
        "create_issue": "forbidden",
        "search_issues": "forbidden",
    }
    # A tool its server does not declare read-only is a write (ADR 0034 §5).
    assert (operations["search_issues"]["access"], operations["create_issue"]["access"]) == ("read", "write")
    assert operations["search_issues"]["input_schema"]["required"] == ["query"]
    (listing,) = fake.received
    assert (listing["kind"], listing["endpoint"]) == ("list_tools", ALIAS)
    assert listing["asserted"]["purpose"] == "discovery"
    assert listing["asserted"]["sub"] == "person:admin@varga.dev"
    assert served.calls == [], "a listing calls no tool"
    assert (await route.connector_row()).status == "ok"
    # No project, so no line of the network journal: the audit log carries a discovery.
    assert await route.journal() == []
    assert [row.action for row in await kit.audit_rows("org.connector.discover")] == [
        "org.connector.discover"
    ]


async def test_a_discovery_the_route_refuses_is_a_409_that_names_the_outpost(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = route.outpost()
    daemon = await daemon_kit.approved(client)
    await route.declare(client)
    discover = f"{ORG}/connectors/{CONNECTOR}/discover"

    def conflict(response: Any, code: str) -> dict[str, Any]:
        """The contract's `OutpostConflict`, with its code, the outpost and the alias."""
        assert response.status_code == 409, response.text
        body = dict(response.json())
        assert (body["code"], body["outpost"], body["endpoint"]) == (code, SLUG, ALIAS), body
        _is_the_contracts("OutpostConflict", body)
        return body

    # Never polled: offline, at once.
    conflict(await client.post(discover), "outpost_offline")
    await route.seen(client, daemon, fake)
    # The outpost's own policy refuses the listing.
    fake.refuse_next("endpoint_unavailable")
    async with route.serving(client, daemon, fake):
        assert conflict(await client.post(discover), "outpost_refused")["refusal"] == "endpoint_unavailable"
        # It takes the listing and never answers.
        fake.lose_lease()
        conflict(await client.post(discover), "outpost_timeout")
    # A quota of the door.
    kit.configure(monkeypatch, outposts_calls_per_minute_per_outpost="2")
    limited = await client.post(discover)
    assert limited.status_code == 429 and limited.json()["retry_after"] == 60, limited.text
    kit.configure(monkeypatch, outposts_calls_per_minute_per_outpost="600")
    # Paused.
    assert (await client.post(f"{ORG}/outposts/{SLUG}/pause")).status_code == 200
    conflict(await client.post(discover), "outpost_paused")
    # The connector is fine, its route is not: it is never marked in error.
    assert (await route.connector_row()).status != "error"
    assert (await route.connector_row()).last_error is None


# ───────────────────────── the gateway's mode `required` ─────────────────────────


async def test_the_gateways_mode_required_does_not_refuse_a_connector_at_an_outpost(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """ADR 0149 §8, amending ADR 0105 §5: `required` keeps upstream credentials and the upstream
    network at the gateway, and through an outpost the platform holds neither."""
    required = await client.put(
        f"{ORG}/mcp-gateway",
        json={
            "mode": "required",
            "endpoint": "https://gw.example.test/{server}/mcp",
            "routing": "path",
            "auth": {"type": "jwt_assertion"},
            "audience": "gw",
        },
    )
    assert required.status_code == 200, required.text
    direct = await client.post(
        f"{ORG}/connectors",
        json={"name": "direct", "type": "mcp", "config": {"url": "https://direct.test/mcp"}},
    )
    assert direct.status_code == 409 and direct.json()["code"] == "gateway_required", direct.text
    # Declared, discovered and called, in the mode that refuses a direct connector each of the three.
    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    async with route.serving(client, daemon, fake):
        assert (await _search(client, headers))["status_code"] == 200
        again = await client.post(f"{ORG}/connectors/{CONNECTOR}/discover")
        assert again.status_code == 200, again.text
    assert len(await route.ledger()) == 1


# ───────────────────────── a governed action's effect ─────────────────────────


async def _effect(project_id: str, operation: str, arguments: dict[str, Any], **action: Any) -> Any:
    from choregos_api.db.models import Action, Project, Run
    from choregos_api.db.session import session_scope
    from choregos_api.effects import EffectContext, effect

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        run = await session.get(Run, RUN)
        assert project is not None and run is not None
        row = Action(
            org_id=project.org_id,
            project_id=project_id,
            run_id=run.id,
            work_item_id=run.work_item_id,
            origin="tool",
            kind=f"{CONNECTOR}.{operation}",
            title="x",
            params={},
            effects=[],
            proposed_by={},
            approval={},
            decisions=[{"by": "ops@varga.dev", "decision": "approve"}],
            status="approved",
            **action,
        )
        session.add(row)
        await session.flush()
        call = {"connector": CONNECTOR, "operation": operation, "arguments": arguments}
        return row.id, await effect("connector.call")(EffectContext(session, row, project), call)


async def test_an_approved_read_is_played_through_the_outpost_with_its_approvers(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    served = route.server(structured={"get_issue": {"key": "OPS-1", "state": "open"}})
    fake = route.outpost(served)
    daemon, _headers = await route.prepared(client, project, fake)
    fake.received.clear()
    async with route.serving(client, daemon, fake):
        action_id, done = await _effect(project["id"], "get_issue", {"key": "OPS-1"})
    assert done == {"key": "OPS-1", "state": "open"}, "what the tool structured, as for any connector"
    (call,) = fake.received
    assert call["asserted"]["action"] == action_id
    assert call["asserted"]["approved_by"] == ["ops@varga.dev"]
    assert (call["asserted"]["purpose"], call["asserted"]["sub"]) == ("action", f"action:{action_id}")
    (row,) = _tool_calls(await route.calls())
    assert (row.action_id, row.write, row.state) == (action_id, False, "answered")


async def test_a_refusal_of_the_route_is_final_for_an_effect(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """In the workers a refusal is an `EffectRefused`: the action fails and compensates, and Temporal
    does not retry it (ADR 0149 §7)."""
    from choregos_api.effects import EffectRefused

    fake = route.outpost()
    daemon, _headers = await route.prepared(client, project, fake)
    fake.refuse_next("tool_not_allowed")
    async with route.serving(client, daemon, fake):
        with pytest.raises(EffectRefused, match="tool_not_allowed"):
            await _effect(project["id"], "get_issue", {})
    assert (await client.post(f"{ORG}/outposts/{SLUG}/pause")).status_code == 200
    with pytest.raises(EffectRefused, match="is paused"):
        await _effect(project["id"], "get_issue", {})


# ───────────────────────── writes: placed once, under the effect's key ─────────────────────────
#
# What the client and the seam do with a write. The rule under which one is opened, the effect that
# passes its key and the action that may end in doubt are S65-14's (`test_write_via_outpost.py`).


async def test_a_direct_connectors_write_is_opened_as_before(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", route.server())
    declared = await client.post(
        f"{ORG}/connectors",
        json={"name": "direct", "type": "mcp", "config": {"url": "https://direct.test/mcp"}},
    )
    assert declared.status_code == 201, declared.text
    assert (await client.post(f"{ORG}/connectors/direct/discover")).status_code == 200
    for policy in ("approval", "allowed"):
        opened = await route.open_operation(client, "create_issue", policy, connector="direct")
        assert opened.status_code == 200, opened.text


async def _open_behind_the_api(operation: str) -> None:
    """A row edited behind the API: what the route must not trust. It states that an in-doubt
    outcome is accepted, as the API asks of whoever opens a write (S65-14): what is left to refuse
    it is the route alone."""
    from choregos_api.db.models import ConnectorOperation
    from choregos_api.db.session import session_scope
    from sqlalchemy import update

    async with session_scope() as session:
        await session.execute(
            update(ConnectorOperation)
            .where(ConnectorOperation.name == operation)
            .values(policy="allowed", in_doubt_accepted=True)
        )


async def test_a_write_that_carries_no_key_is_not_placed(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The lock of the route: a write reaches it without the key of a governed effect (an effect
    played by nothing that journals it, a client built by hand), and it places none."""
    from choregos_api.effects import EffectRefused

    served = route.server()
    fake = route.outpost(served)
    fake.writes_allowed.add(ALIAS)
    daemon, _headers = await route.prepared(client, project, fake)
    from choregos_adapters.outpost import OutpostNotAdmitted

    await _open_behind_the_api("create_issue")
    async with route.serving(client, daemon, fake):
        with pytest.raises(EffectRefused, match="delivered at most once"):
            await _effect(project["id"], "create_issue", {"title": "door stuck"})
        # A tool the connector has no row for is taken for a write too: the prudent default.
        scope, tools = await _write_client(project["id"], None)
        try:
            with pytest.raises(OutpostNotAdmitted) as refused:
                await tools.call_tool("a_tool_nobody_discovered", {})
            assert refused.value.reason == "write_without_key"
            # A read needs no key: it may be asked again.
            assert (await tools.call_tool("get_issue", {}))["isError"] is False
        finally:
            await scope.__aexit__(None, None, None)
    assert [row.tool for row in _tool_calls(await route.calls())] == ["get_issue"]
    assert served.calls == [("get_issue", {})]


async def _write_client(project_id: str, effect_key: str | None) -> Any:
    """The client of the seam, as a governed effect will build it for a write: with its key."""
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.mcp_gateway import connector_client
    from choregos_api.services.mcp_gateway_identity import CallIdentity

    session = session_scope(orgs="*")
    opened = await session.__aenter__()
    project = await opened.get(Project, project_id)
    assert project is not None
    instance = await route.connector_row()

    async def identity() -> CallIdentity:
        return CallIdentity(
            org="varga",
            org_id=instance.org_id,
            subject="action:a-1",
            purpose="action",
            project=project.slug,
            action="a-1",
            connector=instance.name,
        )

    built = await connector_client(opened, instance, identity, effect_key=effect_key)
    return session, built


async def test_a_claimed_write_that_gets_no_answer_is_in_doubt_and_is_never_placed_again(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Delivery at most once (ADR 0149 §5): the write's id comes from its effect's key, so a retry
    meets the first row, in doubt, and nothing is delivered a second time."""
    from choregos_adapters.outpost import OutpostInDoubt

    served = route.server()
    fake = route.outpost(served)
    fake.writes_allowed.add(ALIAS)
    daemon, _headers = await route.prepared(client, project, fake)
    arguments = {"title": "door stuck"}
    fake.lose_lease()
    async with route.serving(client, daemon, fake):
        for attempt in range(2):
            scope, tools = await _write_client(project["id"], "a-1:0")
            try:
                with pytest.raises(OutpostInDoubt) as doubt:
                    await tools.call_tool("create_issue", arguments)
            finally:
                await scope.__aexit__(None, None, None)
            (row,) = _tool_calls(await route.calls())
            assert (row.state, row.write) == ("in_doubt", True), f"attempt {attempt + 1}"
            assert (doubt.value.call_id, doubt.value.instance_id) == (row.id, daemon.instance_id)
            assert doubt.value.claimed_at is not None and doubt.value.tool == "create_issue"
            assert (doubt.value.outpost, doubt.value.endpoint) == (SLUG, ALIAS)
    assert served.calls == [("create_issue", arguments)], "the server received exactly one call"


async def test_a_retried_write_reads_what_the_first_delivery_answered(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import OutpostInstance

    served = route.server(structured={"create_issue": {"key": "OPS-7"}})
    fake = route.outpost(served)
    fake.writes_allowed.add(ALIAS)
    daemon, _headers = await route.prepared(client, project, fake)
    results = []

    async def write(effect_key: str) -> Any:
        scope, tools = await _write_client(project["id"], effect_key)
        try:
            return await tools.call_tool("create_issue", {"title": "door stuck"})
        finally:
            await scope.__aexit__(None, None, None)

    async with route.serving(client, daemon, fake):
        results.append(await write("a-1:0"))
        results.append(await write("a-1:0"))
        # Another effect is another key: another call.
        await write("a-1:1")
    # The outpost has gone away since. What became of the first delivery is on its row: a retry reads
    # it there, and is not told that nobody is there to claim a call it does not place.
    long_ago = datetime(2026, 1, 1, tzinfo=UTC)
    await daemon_kit.change(OutpostInstance, str(daemon.instance_id), last_seen_at=long_ago)
    results.append(await write("a-1:0"))
    assert results[0] == results[1] == results[2] and results[0]["structuredContent"] == {"key": "OPS-7"}
    assert len(served.calls) == 2, "one delivery per key, whatever was retried"
    rows = _tool_calls(await route.calls())
    assert [(row.state, row.write) for row in rows] == [("answered", True)] * 2
    # A write's answer waits for whoever makes its result durable: the client does not empty it.
    assert all(row.answer_sealed is not None for row in rows)


async def test_a_write_nobody_claimed_reached_no_server_and_may_be_placed_again(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """Only a call no instance ever claimed is placed again under its id (ADR 0149 §5): it reached
    no server, so a retry delivers it for the first time. While nobody is there, it is not even
    written again."""
    from choregos_adapters.outpost import OutpostOffline
    from choregos_api.db.models import OutpostInstance

    served = route.server(structured={"create_issue": {"key": "OPS-7"}})
    fake = route.outpost(served)
    fake.writes_allowed.add(ALIAS)
    daemon, _headers = await route.prepared(client, project, fake, timeout_s=TIMEOUT_S)

    async def write() -> Any:
        scope, tools = await _write_client(project["id"], "a-1:0")
        try:
            return await tools.call_tool("create_issue", {"title": "door stuck"})
        finally:
            await scope.__aexit__(None, None, None)

    # The instance polled a moment ago and polls no more: placed, and claimed by nobody.
    with pytest.raises(OutpostOffline):
        await write()
    (row,) = _tool_calls(await route.calls())
    assert (row.state, row.write, row.claimed_at) == ("expired", True, None)
    first_placed = row.created_at
    # Nobody has been there for a long time: the retry is refused at once, and writes nothing.
    await daemon_kit.change(
        OutpostInstance, str(daemon.instance_id), last_seen_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    started = time.monotonic()
    with pytest.raises(OutpostOffline):
        await write()
    assert time.monotonic() - started < TIMEOUT_S - 1
    (row,) = _tool_calls(await route.calls())
    assert (row.state, row.created_at) == ("expired", first_placed), "the row was not placed again"
    assert served.calls == []
    # The outpost is back: the same call, placed again under the same id, delivered once.
    await route.set_timeout(client, route.SETUP_TIMEOUT_S)
    async with route.serving(client, daemon, fake):
        assert (await write())["structuredContent"] == {"key": "OPS-7"}
    (row,) = _tool_calls(await route.calls())
    assert row.state == "answered" and served.calls == [("create_issue", {"title": "door stuck"})]


async def test_an_opened_write_meets_the_routes_refusal_before_any_action_exists(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A write whose row says `allowed` (edited behind the API here) is a governed action the broker
    proposes and waits for. The route is judged first: a paused outpost leaves no action behind."""
    fake = route.outpost()
    _daemon, headers = await route.prepared(client, project, fake)
    await _open_behind_the_api("create_issue")
    assert (await client.post(f"{ORG}/outposts/{SLUG}/pause")).status_code == 200
    written = await client.post(f"{BASE}/{CONNECTOR}__create_issue", headers=headers, json={})
    assert written.status_code == 200, written.text
    _refused(written.json(), 403, "outpost_paused", "network_policy")
    assert await route.actions() == 0 and await route.ledger() == []


async def test_a_conversation_does_not_propose_an_action_at_an_outpost_that_is_handed_nothing(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """An assistant's conversation proposes a governed action for a `console` need (ADR 0035): the
    route is judged first there too, and a refusal is said, not raised."""
    from types import SimpleNamespace

    from choregos_api.db.session import session_scope
    from choregos_api.services.conversation_actions import _propose_governed

    fake = route.outpost()
    await route.prepared(client, project, fake)
    assert (await client.post(f"{ORG}/outposts/{SLUG}/pause")).status_code == 200
    record = SimpleNamespace(project_id=project["id"])
    entry = SimpleNamespace(tool=SimpleNamespace(connector=await route.connector_row()))
    async with session_scope(orgs="*") as session:
        said = await _propose_governed(session, record, entry, {})  # type: ignore[arg-type]
    assert isinstance(said, str) and said.startswith("action not proposed: ") and SLUG in said
    assert await route.actions() == 0


async def test_a_refusal_at_admission_does_not_keep_the_organisations_placements_waiting(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An admission is judged under a lock that serialises the organisation's placements, until the
    transaction that took it ends. A call that is refused ends it there, not when its caller's
    request does: the caller may go on for as long as it likes, and nobody waits behind it."""
    from choregos_adapters.outpost import OutpostNotAdmitted

    fake = route.outpost()
    daemon, _headers = await route.prepared(client, project, fake)
    await route.seen(client, daemon, fake)
    kit.configure(monkeypatch, outposts_calls_per_minute_per_outpost="1")  # the discovery was the one
    scope, tools = await _write_client(project["id"], "a-1:0")
    try:
        with pytest.raises(OutpostNotAdmitted) as refused:
            await tools.call_tool("get_issue", {})
        assert refused.value.reason == "outpost_rate"
        # The caller's session is still open. Another placement of the organisation is judged at once.
        kit.configure(monkeypatch, outposts_calls_per_minute_per_outpost="600")
        placed = await asyncio.wait_for(daemon_kit.place(), timeout=3)
        assert placed.created
    finally:
        await scope.__aexit__(None, None, None)


async def test_the_route_reads_the_outposts_state_again_at_every_call(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """A pause is a commit away: a session that already read the outpost does not keep what it read."""
    from choregos_adapters.outpost import OutpostPaused
    from choregos_api.db.session import session_scope
    from choregos_api.services import outpost_route

    fake = route.outpost()
    await route.prepared(client, project, fake)
    instance = await route.connector_row()
    async with session_scope(orgs="*") as session:
        reached = await outpost_route.route(session, instance)
        assert (reached.name, reached.endpoint) == (SLUG, ALIAS)
        assert (await client.post(f"{ORG}/outposts/{SLUG}/pause")).status_code == 200
        with pytest.raises(OutpostPaused, match="is paused"):
            await outpost_route.route(session, instance)


# ───────────────────────── the network bounds and the journal ─────────────────────────


async def test_each_call_is_journaled_under_the_outpost_and_the_alias_and_the_alias_is_no_host(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """ADR 0149 §11: the platform opens no connection, so the organisation's allow-list, deny list and
    per-host rate have no host to look at; the journal still shows that the tool was reached, and
    where. The alias is a name in the outpost's policy file: a deny rule that bears it denies nothing."""
    ceiling = await client.put(
        f"{ORG}/policy-ceiling",
        json={"ceiling": {"network": {"deny_domains": [ALIAS], "per_host_per_minute": 1}}},
    )
    assert ceiling.status_code == 200, ceiling.text
    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)  # declared under that ceiling
    async with route.serving(client, daemon, fake):
        for _ in range(3):
            assert (await _search(client, headers))["status_code"] == 200
    assert await route.journal() == [(HOST, "broker", "allowed", "", 3)]
    assert len(await route.ledger()) == 3
