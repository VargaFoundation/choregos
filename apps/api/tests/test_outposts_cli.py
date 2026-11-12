# SPDX-License-Identifier: Apache-2.0
"""`choregos outposts` against the real management routes (S65-17a), in process.

Each command runs through the CLI's own Typer app and its own client, the one that turns an API
refusal into a red line and exit code 1; only the socket is replaced, by the test's ASGI application.
The CLI is synchronous, so a command runs in a thread while the event loop serves its requests
(the way `site_pages.run_cli` runs a documentation page's commands).

A person's CLI holds an API token, so that is what these commands hold here. The one command a token
cannot carry out is `approve`: the API refuses it, and the CLI prints that refusal as it is. What the
operator of an outpost pastes (the policy file's lines, the enrolment command) is checked against the
contract's own model of a policy file and against the daemon's own command line.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
import typer
import yaml
from choregos_contracts import OutpostPolicy
from httpx import ASGITransport, AsyncClient, Response
from typer.testing import CliRunner, Result

from .kits import outposts as kit

SLUG = "factory-floor"
ROUTES = f"/api/v1/orgs/{kit.ORG}/outposts"


class _Wire:
    """Where the CLI's client sends its requests: the test application, with what `http` holds."""

    def __init__(self, http: AsyncClient, loop: asyncio.AbstractEventLoop) -> None:
        self._http, self._loop = http, loop

    def request(self, method: str, path: str, **kwargs: Any) -> Response:
        call = self._http.request(method, "/api/v1" + path, **kwargs)
        return asyncio.run_coroutine_threadsafe(call, self._loop).result(30)

    def close(self) -> None:
        """The test owns `http`."""


async def cli(http: AsyncClient, *words: str) -> Result:
    """Run `choregos outposts <words>` for the organisation of the fixtures."""
    from choregos_cli import main

    loop = asyncio.get_running_loop()
    the_cli_s_own = main.client

    def connected() -> Any:
        api = the_cli_s_own()
        api._client.close()
        api._client = _Wire(http, loop)
        return api

    def invoke() -> Result:
        with mock.patch.object(main, "client", connected):
            return CliRunner().invoke(main.app, ["outposts", *words, "--org", kit.ORG])

    return await asyncio.to_thread(invoke)


def said(result: Result) -> str:
    """What a command printed, on one line: the console folds a long sentence at the terminal's width."""
    return " ".join(result.output.split())


def _expiry(issued: Result) -> datetime:
    """When the token a `token` command printed expires. The application logs its requests on the same
    standard output the runner captures: the line is looked for, not counted."""
    stamp = re.search(r"single use, expires (\S+)$", issued.output, re.M)
    assert stamp, issued.output
    return datetime.fromisoformat(stamp.group(1))


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_cli import client as client_module

    # No profile file and no version check: the CLI of whoever runs the suite is left alone.
    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv("CHOREGOS_NO_VERSION_CHECK", "1")
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    return kit.switch_on(monkeypatch)


@pytest.fixture
async def token_holder(app: Any, client: AsyncClient, admin: str) -> AsyncIterator[AsyncClient]:
    """The administrator as their CLI knows them: an API token of scope `*`, no session."""
    created = await client.post("/api/v1/me/tokens", json={"name": "cli", "scopes": ["*"]})
    assert created.status_code == 201, created.text
    headers = {"Authorization": f"Bearer {created.json()['token']}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as http:
        yield http


async def test_the_switch_and_the_missing_key_are_said_in_the_apis_words(
    token_holder: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    off = await cli(token_holder, "create", SLUG)
    assert off.exit_code == 1
    assert "409 — outposts are switched off on this deployment (`CHOREGOS_OUTPOSTS`)" in said(off)

    kit.configure(monkeypatch, outposts="on", outposts_door_url=kit.ORIGIN)
    assert (await cli(token_holder, "create", SLUG)).exit_code == 0
    no_key = await cli(token_holder, "token", SLUG)
    assert no_key.exit_code == 1
    assert "409 — the outposts' door has no signing key on this deployment" in said(no_key)
    assert "choregos-outpost enrol" not in no_key.output
    shown = await cli(token_holder, "show", SLUG)
    assert shown.exit_code == 0 and "No key to pin" in shown.output and "door_keys:" not in shown.output


async def test_an_outpost_is_created_listed_and_shown_with_the_keys_to_pin(
    client: AsyncClient, token_holder: AsyncClient, door: kit.Door
) -> None:
    made = await cli(token_holder, "create", SLUG, "--display-name", "The factory floor")
    assert made.exit_code == 0, made.output
    assert f"outpost `{SLUG}` created: active, no instance yet" in made.output
    stored = (await client.get(f"{ROUTES}/{SLUG}")).json()
    assert (stored["display_name"], stored["status"]) == ("The factory floor", "active")

    listed = await cli(token_holder, "list")
    assert listed.exit_code == 0, listed.output
    (row,) = [line for line in listed.output.splitlines() if SLUG in line]
    cells = [cell.strip() for cell in row.split("│")[1:-1]]
    assert cells == [SLUG, "The factory floor", "active", "0", "0", "0", "never seen", "—", "—"]

    shown = await cli(token_holder, "show", SLUG)
    assert shown.exit_code == 0, shown.output
    assert "status: active · epoch 0 · capabilities: tools" in shown.output
    (line,) = [text for text in shown.output.splitlines() if text.startswith("door_keys:")]
    assert yaml.safe_load(line) == {"door_keys": door.keys}, (
        "the key that signs first, then the announced one"
    )

    taken = await cli(token_holder, "create", SLUG)
    assert taken.exit_code == 1 and f"409 — `{SLUG}` is already an outpost of {kit.ORG}" in said(taken)
    unknown = await cli(token_holder, "show", "nowhere")
    assert unknown.exit_code == 1 and "404" in unknown.output


async def test_the_token_command_prints_what_the_operator_pastes(
    client: AsyncClient, token_holder: AsyncClient, door: kit.Door
) -> None:
    """The lines are a policy file's, the command is the daemon's, and the token enrols an instance."""
    from choregos_outpost.cli import app as daemon

    await kit.create(client, SLUG)
    before = datetime.now(UTC)
    issued = await cli(token_holder, "token", SLUG)
    assert issued.exit_code == 0, issued.output
    lines = issued.output.splitlines()

    lifetime = _expiry(issued) - before
    assert timedelta(minutes=14, seconds=50) < lifetime <= timedelta(minutes=15, seconds=5)

    pasted = [line for line in lines if re.match(r"(control_plane|door_keys):", line)]
    assert len(pasted) == 2, issued.output
    written = yaml.safe_load("\n".join(pasted))
    assert written == {"control_plane": kit.ORIGIN, "door_keys": door.keys}
    # With the operator's own endpoints and audit log, what was pasted is a policy file the daemon takes.
    policy = OutpostPolicy.model_validate(
        written | {"endpoints": {}, "audit": {"file": "/var/log/choregos-outpost/audit.jsonl"}}
    )
    assert policy.door_keys == door.keys

    (command,) = [line for line in lines if line.startswith("choregos-outpost ")]
    words = command.split()
    assert words[:3] == ["choregos-outpost", "enrol", "--token"] and len(words) == 4
    token = words[3]
    assert token in lines, "the token is also on a line of its own"
    # The command is one the daemon's own command line takes. Its options are read from its declaration:
    # running the daemon's program here would set the logging of the whole process to its own.
    enrol = typer.main.get_command(daemon).commands["enrol"]
    assert "--token" in {name for parameter in enrol.params for name in parameter.opts}
    assert f"Administration → Outposts → {SLUG}" in issued.output

    # The token is a real one: it enrols an instance, born pending, and is then used up.
    enrolled = await kit.enrol(token)
    assert (enrolled.instance.status, enrolled.outpost.slug) == ("pending", SLUG)
    again = await client.get(f"{ROUTES}/{SLUG}/instances")
    assert token not in again.text and [i["status"] for i in again.json()["items"]] == ["pending"]

    short = await cli(token_holder, "token", SLUG, "--expires-in-minutes", "5")
    assert short.exit_code == 0, short.output
    assert _expiry(short) - datetime.now(UTC) <= timedelta(minutes=5)
    too_long = await cli(token_holder, "token", SLUG, "--expires-in-minutes", "16")
    assert too_long.exit_code == 1 and "422" in too_long.output


async def test_a_door_with_no_origin_leaves_a_comment_where_the_line_would_be(
    client: AsyncClient, token_holder: AsyncClient, door: kit.Door, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit.configure(monkeypatch, outposts_door_url=None)
    await kit.create(client, SLUG)
    issued = await cli(token_holder, "token", SLUG)
    assert issued.exit_code == 0, issued.output
    lines = issued.output.splitlines()
    assert not [line for line in lines if line.startswith("control_plane:")], "no origin is invented"
    assert [line for line in lines if line.startswith("# control_plane:")], issued.output
    assert yaml.safe_load("\n".join(line for line in lines if line.startswith(("#", "door_keys:")))) == {
        "door_keys": door.keys
    }


async def test_instances_are_listed_and_a_token_cannot_approve(
    client: AsyncClient, token_holder: AsyncClient, door: kit.Door
) -> None:
    """Enrolment needs a person twice (ADR 0147 §4): the CLI holds a token, and says what the API said."""
    await kit.create(client, SLUG)
    none = await cli(token_holder, "instances", SLUG)
    assert none.exit_code == 0 and f"choregos outposts token {SLUG}" in none.output

    instance = await kit.enrolled_instance(client, SLUG, facts={"hostname": "outpost-0"})
    fingerprint = await kit.fingerprint_of_instance(client, SLUG, instance)
    listed = await cli(token_holder, "instances", SLUG)
    assert listed.exit_code == 0, listed.output
    lines = listed.output.splitlines()
    assert f"pending  {instance}" in lines and f"  fingerprint: {fingerprint}" in lines
    assert "  what it said of itself: hostname=outpost-0" in lines
    assert f"approved in the console (Administration → Outposts → {SLUG})" in said(listed)

    refused = await cli(token_holder, "approve", SLUG, instance, "--fingerprint", fingerprint)
    assert refused.exit_code == 1, refused.output
    assert (
        "403 — an instance is approved in a person's session, not with an API token "
        "(approval_requires_session)" in said(refused)
    )
    (still,) = (await client.get(f"{ROUTES}/{SLUG}/instances")).json()["items"]
    assert still["status"] == "pending" and await kit.audit_rows("outpost.approve") == []


async def test_approve_carries_the_fingerprint_it_was_given_and_nothing_else(
    client: AsyncClient, door: kit.Door, admin: str
) -> None:
    """With a person's session in place of the token, the same command is the route's own request:
    another fingerprint is refused in the API's words, the daemon's own is taken."""
    from choregos_contracts import fingerprint_of

    await kit.create(client, SLUG)
    instance = await kit.enrolled_instance(client, SLUG)
    fingerprint = await kit.fingerprint_of_instance(client, SLUG, instance)

    another = fingerprint_of(kit.signing_key())
    mismatch = await cli(client, "approve", SLUG, instance, "--fingerprint", another)
    assert mismatch.exit_code == 1
    assert "409 — the fingerprint sent is not the one this instance enrolled with" in said(mismatch)
    malformed = await cli(client, "approve", SLUG, instance, "--fingerprint", "09wT63")
    assert malformed.exit_code == 1 and "422" in malformed.output
    missing = await cli(client, "approve", SLUG, instance)
    assert missing.exit_code == 2, "the fingerprint is not optional"
    (still,) = (await client.get(f"{ROUTES}/{SLUG}/instances")).json()["items"]
    assert still["status"] == "pending"

    approved = await cli(client, "approve", SLUG, instance, "--fingerprint", fingerprint)
    assert approved.exit_code == 0, approved.output
    assert f"instance {instance} of `{SLUG}` is active, approved by {admin}" in said(approved)
    (entry,) = await kit.audit_rows("outpost.approve")
    assert entry.payload["fingerprint"] == fingerprint


async def test_pause_resume_revoke_an_instance_revoke_and_delete(
    client: AsyncClient, token_holder: AsyncClient, door: kit.Door
) -> None:
    await kit.create(client, SLUG)
    kept = await kit.enrolled_instance(client, SLUG)
    await kit.approve(client, SLUG, kept)
    gone = await kit.enrolled_instance(client, SLUG)

    async def status() -> tuple[str, int]:
        outpost = (await client.get(f"{ROUTES}/{SLUG}")).json()
        return outpost["status"], outpost["epoch"]

    paused = await cli(token_holder, "pause", SLUG)
    assert paused.exit_code == 0 and f"`{SLUG}` is paused: nothing more is handed to it" in paused.output
    assert await status() == ("paused", 0)
    (row,) = [line for line in (await cli(token_holder, "list")).output.splitlines() if SLUG in line]
    assert [cell.strip() for cell in row.split("│")[1:-1]][2:6] == ["paused", "1", "1", "0"]
    resumed = await cli(token_holder, "resume", SLUG)
    assert resumed.exit_code == 0 and f"`{SLUG}` is active" in resumed.output
    assert await status() == ("active", 0)

    one = await cli(token_holder, "revoke-instance", SLUG, gone)
    assert one.exit_code == 0, one.output
    assert f"instance {gone} of `{SLUG}` is revoked. This is final." in said(one)
    assert await status() == ("active", 1), "the revocation of an instance moves the epoch"
    statuses = {
        i["id"]: i["status"] for i in (await client.get(f"{ROUTES}/{SLUG}/instances")).json()["items"]
    }
    assert statuses == {kept: "active", gone: "revoked"}
    unknown = await cli(token_holder, "revoke-instance", SLUG, "0" * 64)
    assert unknown.exit_code == 1 and "404" in unknown.output

    early = await cli(token_holder, "delete", SLUG)
    assert early.exit_code == 1 and f"409 — `{SLUG}` is active: revoke it first" in said(early)
    revoked = await cli(token_holder, "revoke", SLUG)
    assert revoked.exit_code == 0, revoked.output
    assert f"`{SLUG}` is revoked, with every instance of it (epoch 2). This is final." in said(revoked)
    assert await status() == ("revoked", 2)
    late = await cli(token_holder, "pause", SLUG)
    assert late.exit_code == 1 and f"409 — `{SLUG}` is revoked: that is final" in said(late)

    await kit.connector_at(SLUG)
    in_use = await cli(token_holder, "delete", SLUG)
    assert in_use.exit_code == 1
    assert f"409 — `{SLUG}` still has connectors that route to it" in said(in_use)
    shown = await cli(token_holder, "show", SLUG)
    assert "connectors that route to it: tracker-at-the-outpost" in shown.output, "`show` names them"
    assert (await client.get(f"{ROUTES}/{SLUG}")).status_code == 200, "still there"


async def test_a_revoked_outpost_is_deleted(
    client: AsyncClient, token_holder: AsyncClient, door: kit.Door
) -> None:
    await kit.create(client, SLUG)
    assert (await cli(token_holder, "revoke", SLUG)).exit_code == 0
    deleted = await cli(token_holder, "delete", SLUG)
    assert deleted.exit_code == 0 and f"outpost `{SLUG}` deleted" in deleted.output
    assert (await client.get(f"{ROUTES}/{SLUG}")).status_code == 404
    assert SLUG not in (await cli(token_holder, "list")).output


async def _call(slug: str, call_id: str, **columns: Any) -> None:
    """A call routed to the outpost, as the broker will write one (S65-13): written by hand."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.db.session import name_the_outpost, session_scope

    outpost = await kit.outpost_row(slug)
    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, outpost.id)
        session.add(OutpostCall(id=call_id, org_id=outpost.org_id, outpost_id=outpost.id, **columns))


async def test_calls_shows_states_and_refusal_codes_and_no_payload(
    client: AsyncClient, token_holder: AsyncClient, door: kit.Door
) -> None:
    await kit.create(client, SLUG)
    now = datetime.now(UTC)
    digest = "a1b2c3d4e5f6" + "0" * 52
    await _call(
        SLUG,
        "call-1",
        kind="call_tool",
        state="answered",
        endpoint="tracker",
        tool="search_issues",
        write=False,
        outcome="result",
        arguments_sha256=digest,
        created_at=now - timedelta(minutes=2),
    )
    await _call(
        SLUG,
        "call-2",
        kind="call_tool",
        state="refused",
        endpoint="tracker",
        tool="create_issue",
        write=True,
        refusal="write_denied",
        arguments_sha256=digest,
        created_at=now - timedelta(minutes=1),
    )
    await _call(
        SLUG, "call-3", kind="list_tools", state="expired", endpoint="tracker", write=False, created_at=now
    )

    def rows(result: Result) -> list[list[str]]:
        table = [line for line in result.output.splitlines() if "│" in line]
        return [[cell.strip() for cell in line.split("│")[1:-1]][1:] for line in table]

    listed = await cli(token_holder, "calls", SLUG)
    assert listed.exit_code == 0, listed.output
    assert rows(listed) == [
        ["(tool listing)", "tracker", "—", "expired", "—", "—", "read", "—"],
        ["create_issue", "tracker", "—", "refused", "—", "write_denied", "write", "a1b2c3d4e5f6"],
        ["search_issues", "tracker", "—", "answered", "result", "—", "read", "a1b2c3d4e5f6"],
    ], "newest first"
    assert "--cursor" not in listed.output

    refused = await cli(token_holder, "calls", SLUG, "--state", "refused")
    assert [row[0] for row in rows(refused)] == ["create_issue"]
    first = await cli(token_holder, "calls", SLUG, "--limit", "2")
    assert [row[0] for row in rows(first)] == ["(tool listing)", "create_issue"]
    more = re.search(rf"choregos outposts calls {SLUG} --cursor (\S+)", first.output)
    assert more, first.output
    second = await cli(token_holder, "calls", SLUG, "--limit", "2", "--cursor", more.group(1))
    assert [row[0] for row in rows(second)] == ["search_issues"] and "--cursor" not in second.output
    wrong = await cli(token_holder, "calls", SLUG, "--state", "lost")
    assert wrong.exit_code == 1 and "422" in wrong.output
