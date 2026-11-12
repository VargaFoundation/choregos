# SPDX-License-Identifier: Apache-2.0
"""`choregos outposts` (S65-17a, ADR 0147): what each command asks the API, and what it prints.

The API is mocked here with respx, call by call; `apps/api/tests/test_outposts_cli.py` drives the same
commands against the real management routes, in process.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import respx
import yaml
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
BASE = f"{API}/api/v1/orgs/acme/outposts"
ONE = f"{BASE}/factory-floor"
KEYS = ["3k9fZti-P3Z9Du9RmNmJm-pNxgUQSHfGN_bx7DmXwNw", "kPu0v5q0mTqH1cQ7m9cN2rY8wXhJ4bS6dF3gA1zL0eE"]
TOKEN = "choe_" + "0192a3b4c5d6e7f8a9b0c1d2e3f4a5b6" + "s3-_" * 10 + "abc"
INSTANCE = "0192a3b4c5d6e7f8a9b0c1d2e3f4a5b6" + "0192a3b4c5d6e7f8a9b0c1d2e3f4a5c7"
FINGERPRINT = "SHA256:" + "f1Ng3rPr1nt" * 3 + "0123456789"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_cli import client as client_module

    # No profile file: the profile is the environment's, never the one of whoever runs the suite.
    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_TOKEN", "chg_test")
    monkeypatch.setenv("CHOREGOS_ORG", "acme")
    monkeypatch.setenv("COLUMNS", "200")


def _run(*words: str) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, ["outposts", *words])


def _plain(text: str) -> str:
    """A help text as a person reads it: no styling (a CI runner styles it), no box, one line."""
    return " ".join(ANSI.sub("", text).replace("│", " ").split())


def _outpost(**changes: Any) -> dict[str, Any]:
    return {
        "slug": "factory-floor",
        "display_name": "The factory floor",
        "status": "active",
        "capabilities": ["tools"],
        "epoch": 0,
        "online": True,
        "last_seen_at": "2026-11-12T10:00:00Z",
        "instances": {"pending": 1, "active": 2, "revoked": 3},
        "endpoints": ["tracker"],
        "connectors": ["tracker-at-the-outpost"],
        "door_keys": KEYS,
        "created_by": "ada@example.com",
        "created_at": "2026-11-11T09:00:00Z",
    } | changes


def _instance(**changes: Any) -> dict[str, Any]:
    return {
        "id": INSTANCE,
        "status": "pending",
        "fingerprint": FINGERPRINT,
        "daemon_version": "0.27.0",
        "protocol": 1,
        "capabilities": ["tools"],
        "endpoints": [],
        "facts": {"platform": "linux/amd64", "hostname": "outpost-0.tools.svc"},
        "enrolled_at": "2026-11-12T09:58:00Z",
        "approved_by": None,
        "approved_at": None,
        "revoked_by": None,
        "revoked_at": None,
        "last_seen_at": None,
        "online": False,
        "in_flight": 0,
    } | changes


def _created(**changes: Any) -> dict[str, Any]:
    return {
        "id": "enr-1",
        "token": TOKEN,
        "expires_at": "2026-11-12T10:15:00Z",
        "control_plane": "https://outposts.example.com",
        "door_keys": KEYS,
    } | changes


# ───────────────────────── an outpost ─────────────────────────


@respx.mock
def test_create_sends_the_slug_and_says_how_to_enrol_an_instance() -> None:
    route = respx.post(BASE).mock(return_value=Response(201, json=_outpost(online=False)))
    made = _run("create", "factory-floor", "--display-name", "The factory floor")
    assert made.exit_code == 0, made.output
    assert json.loads(route.calls[0].request.content) == {
        "slug": "factory-floor",
        "display_name": "The factory floor",
    }
    assert "outpost `factory-floor` created: active" in made.output
    assert "choregos outposts token factory-floor" in made.output
    bare = _run("create", "cold-store")
    assert bare.exit_code == 0 and json.loads(route.calls[1].request.content) == {"slug": "cold-store"}


@respx.mock
def test_a_creation_refused_because_outposts_are_off_prints_the_apis_words() -> None:
    words = "outposts are switched off on this deployment (`CHOREGOS_OUTPOSTS`): its operator turns them on"
    respx.post(BASE).mock(
        return_value=Response(409, json={"title": "Conflict", "detail": words, "code": "outposts_disabled"})
    )
    refused = _run("create", "factory-floor")
    assert refused.exit_code == 1
    assert f"409 — {words}" in " ".join(refused.output.split())


@respx.mock
def test_list_shows_status_instances_and_presence() -> None:
    respx.get(BASE).mock(
        return_value=Response(
            200,
            json={
                "items": [
                    _outpost(),
                    _outpost(
                        slug="cold-store",
                        display_name="Cold store",
                        status="paused",
                        online=False,
                        instances={"pending": 0, "active": 1, "revoked": 0},
                        endpoints=[],
                        connectors=[],
                    ),
                    _outpost(slug="new", display_name="new", online=False, last_seen_at=None),
                ]
            },
        )
    )
    shown = _run("list")
    assert shown.exit_code == 0, shown.output
    rows = {
        cells[0]: cells[1:]
        for cells in ([c.strip() for c in line.split("│")[1:-1]] for line in shown.output.splitlines())
        if cells
    }
    assert rows["factory-floor"] == [
        "The factory floor",
        "active",
        "2",
        "1",
        "3",
        "online",
        "tracker",
        "tracker-at-the-outpost",
    ]
    assert rows["cold-store"][1:] == ["paused", "1", "0", "0", "last seen 2026-11-12T10:00:00Z", "—", "—"]
    assert rows["new"][5] == "never seen"


@respx.mock
def test_show_prints_the_keys_to_pin_as_the_policy_files_line() -> None:
    respx.get(ONE).mock(return_value=Response(200, json=_outpost()))
    shown = _run("show", "factory-floor")
    assert shown.exit_code == 0, shown.output
    assert "status: active · epoch 0 · capabilities: tools" in shown.output
    assert "instances: 2 approved, 1 pending, 3 revoked" in shown.output
    assert "connectors that route to it: tracker-at-the-outpost" in shown.output
    (line,) = [text for text in shown.output.splitlines() if text.startswith("door_keys:")]
    assert yaml.safe_load(line) == {"door_keys": KEYS}
    assert "/etc/choregos-outpost/policy.yaml" in shown.output


@respx.mock
def test_show_says_so_when_the_door_has_no_key_to_pin() -> None:
    respx.get(ONE).mock(return_value=Response(200, json=_outpost(door_keys=[])))
    shown = _run("show", "factory-floor")
    assert shown.exit_code == 0, shown.output
    assert "No key to pin" in shown.output and "door_keys:" not in shown.output


@respx.mock
def test_pause_resume_revoke_and_delete_each_call_their_route() -> None:
    pause = respx.post(f"{ONE}/pause").mock(return_value=Response(200, json=_outpost(status="paused")))
    resume = respx.post(f"{ONE}/resume").mock(return_value=Response(200, json=_outpost()))
    revoke = respx.post(f"{ONE}/revoke").mock(
        return_value=Response(200, json=_outpost(status="revoked", epoch=4))
    )
    delete = respx.delete(ONE).mock(return_value=Response(204))
    paused = _run("pause", "factory-floor")
    assert (
        paused.exit_code == 0 and "`factory-floor` is paused: nothing more is handed to it" in paused.output
    )
    resumed = _run("resume", "factory-floor")
    assert resumed.exit_code == 0 and "`factory-floor` is active" in resumed.output
    revoked = _run("revoke", "factory-floor")
    assert revoked.exit_code == 0, revoked.output
    assert "is revoked, with every instance of it (epoch 4). This is final." in revoked.output
    deleted = _run("delete", "factory-floor")
    assert deleted.exit_code == 0 and "outpost `factory-floor` deleted" in deleted.output
    assert [route.call_count for route in (pause, resume, revoke, delete)] == [1, 1, 1, 1]
    assert not pause.calls[0].request.content, "a gesture carries no body"


@respx.mock
@pytest.mark.parametrize(
    ("code", "words"),
    [
        ("outpost_in_use", "`factory-floor` still has connectors that route to it: route them elsewhere"),
        ("outpost_not_revoked", "`factory-floor` is active: revoke it first"),
    ],
)
def test_a_refused_deletion_prints_the_apis_refusal(code: str, words: str) -> None:
    respx.delete(ONE).mock(
        return_value=Response(409, json={"title": "Conflict", "detail": words, "code": code})
    )
    refused = _run("delete", "factory-floor")
    assert refused.exit_code == 1
    assert f"409 — {words}" in " ".join(refused.output.split())
    assert "deleted" not in refused.output


# ───────────────────────── an enrolment token ─────────────────────────


@respx.mock
def test_token_prints_the_token_once_its_expiry_the_policy_lines_and_the_enrol_command() -> None:
    route = respx.post(f"{ONE}/enrolments").mock(return_value=Response(201, json=_created()))
    issued = _run("token", "factory-floor")
    assert issued.exit_code == 0, issued.output
    assert json.loads(route.calls[0].request.content) == {}, "the API's own default lifetime"
    lines = issued.output.splitlines()
    assert "single use, expires 2026-11-12T10:15:00Z" in lines[0]
    assert TOKEN in lines, "the token on a line of its own"
    # The two lines an operator pastes are a policy file's, as they are.
    policy = yaml.safe_load(
        "\n".join(text for text in lines if re.match(r"(control_plane|door_keys):", text))
    )
    assert policy == {"control_plane": "https://outposts.example.com", "door_keys": KEYS}
    assert f"choregos-outpost enrol --token {TOKEN}" in lines, "the command, ready to run"
    assert "/etc/choregos-outpost/policy.yaml" in issued.output
    assert "Administration → Outposts → factory-floor" in issued.output
    assert "only if the fingerprint shown there is that one" in issued.output


@respx.mock
def test_what_is_pasted_is_never_cut_by_the_terminals_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token or a key folded at 40 columns is one nobody can paste."""
    monkeypatch.setenv("COLUMNS", "40")
    respx.post(f"{ONE}/enrolments").mock(return_value=Response(201, json=_created()))
    respx.get(ONE).mock(return_value=Response(200, json=_outpost()))
    respx.get(f"{ONE}/instances").mock(return_value=Response(200, json={"items": [_instance()]}))
    issued = _run("token", "factory-floor").output.splitlines()
    assert TOKEN in issued and f"choregos-outpost enrol --token {TOKEN}" in issued
    assert 'door_keys: ["' + '", "'.join(KEYS) + '"]' in issued
    assert 'door_keys: ["' + '", "'.join(KEYS) + '"]' in _run("show", "factory-floor").output.splitlines()
    listed = _run("instances", "factory-floor").output.splitlines()
    assert f"pending  {INSTANCE}" in listed and f"  fingerprint: {FINGERPRINT}" in listed


@respx.mock
def test_token_passes_a_shorter_lifetime_and_marks_a_door_with_no_origin() -> None:
    route = respx.post(f"{ONE}/enrolments").mock(
        return_value=Response(201, json=_created(control_plane=None, door_keys=KEYS[:1]))
    )
    issued = _run("token", "factory-floor", "--expires-in-minutes", "5")
    assert issued.exit_code == 0, issued.output
    assert json.loads(route.calls[0].request.content) == {"expires_in_minutes": 5}
    lines = issued.output.splitlines()
    assert not any(line.startswith("control_plane:") for line in lines), "no value is invented"
    (comment,) = [line for line in lines if line.startswith("# control_plane:")]
    assert "declares no origin" in comment
    assert yaml.safe_load("\n".join(line for line in lines if line.startswith(("#", "door_keys")))) == {
        "door_keys": KEYS[:1]
    }


@respx.mock
def test_a_token_refused_for_want_of_a_door_key_prints_the_apis_words() -> None:
    words = (
        "the outposts' door has no signing key on this deployment (`CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY`): "
        "there is no key to pin, so no instance is enrolled"
    )
    respx.post(f"{ONE}/enrolments").mock(
        return_value=Response(409, json={"title": "Conflict", "detail": words, "code": "door_key_missing"})
    )
    refused = _run("token", "factory-floor")
    assert refused.exit_code == 1
    assert f"409 — {words}" in " ".join(refused.output.split())
    assert "choregos-outpost enrol" not in refused.output


# ───────────────────────── instances ─────────────────────────


@respx.mock
def test_instances_shows_each_fingerprint_and_where_a_pending_one_is_approved() -> None:
    active = _instance(
        id="b" * 64,
        status="active",
        endpoints=["tracker"],
        approved_by="ada@example.com",
        approved_at="2026-11-12T10:01:00Z",
        last_seen_at="2026-11-12T10:02:00Z",
        online=True,
        in_flight=2,
    )
    revoked = _instance(
        id="c" * 64, status="revoked", revoked_by="ada@example.com", revoked_at="2026-11-12T10:03:00Z"
    )
    respx.get(f"{ONE}/instances").mock(
        return_value=Response(200, json={"items": [_instance(), active, revoked]})
    )
    shown = _run("instances", "factory-floor")
    assert shown.exit_code == 0, shown.output
    lines = shown.output.splitlines()
    assert [line for line in lines if not line.startswith(" ") and line][:3] == [
        f"pending  {INSTANCE}",
        f"active  {'b' * 64}",
        f"revoked  {'c' * 64}",
    ], "in the API's order: pending ones first"
    assert f"  fingerprint: {FINGERPRINT}" in lines
    assert "  what it said of itself: hostname=outpost-0.tools.svc platform=linux/amd64" in lines
    assert "  daemon 0.27.0, protocol 1 · online · 2 call(s) in flight" in lines
    assert "  endpoints: tracker" in lines
    assert "  approved 2026-11-12T10:01:00Z by ada@example.com" in lines
    assert "  revoked 2026-11-12T10:03:00Z by ada@example.com" in lines
    assert "approved in the console (Administration → Outposts → factory-floor)" in shown.output


@respx.mock
def test_instances_says_how_to_enrol_the_first_one_and_does_not_send_to_the_console_for_nothing() -> None:
    route = respx.get(f"{ONE}/instances")
    route.mock(return_value=Response(200, json={"items": []}))
    assert "choregos outposts token factory-floor" in _run("instances", "factory-floor").output
    route.mock(return_value=Response(200, json={"items": [_instance(status="active")]}))
    assert "approved in the console" not in _run("instances", "factory-floor").output


@pytest.mark.parametrize("on_a_ci_runner", [False, True])
def test_the_help_of_approve_says_a_token_cannot_approve(
    on_a_ci_runner: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The help is styled on a CI runner: it is read without its styling, and tried both ways."""
    if on_a_ci_runner:
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
    else:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    shown = _run("approve", "--help")
    assert shown.exit_code == 0, shown.output
    text = _plain(shown.output)
    assert "refuses an API token (403)" in text
    assert "a person's session that carries a recent sign-in" in text
    assert "prints the API's refusal as it is" in text
    assert "Administration → Outposts" in text
    assert "--fingerprint" in text and "[required]" in text


@respx.mock
def test_approve_sends_the_fingerprint_given_and_prints_the_apis_refusal_as_it_is() -> None:
    words = "an instance is approved in a person's session, not with an API token (approval_requires_session)"
    route = respx.post(f"{ONE}/instances/{INSTANCE}/approve").mock(
        return_value=Response(403, json={"title": "Forbidden", "detail": words, "status": 403})
    )
    refused = _run("approve", "factory-floor", INSTANCE, "--fingerprint", FINGERPRINT)
    assert refused.exit_code == 1
    assert json.loads(route.calls[0].request.content) == {"fingerprint": FINGERPRINT}
    assert route.calls[0].request.headers["authorization"] == "Bearer chg_test", "no way around: the token"
    assert f"403 — {words}" in " ".join(refused.output.split())
    assert route.call_count == 1, "refused once, never tried another way"


@respx.mock
def test_approve_needs_a_fingerprint_and_says_what_the_api_answered_when_it_accepts() -> None:
    route = respx.post(f"{ONE}/instances/{INSTANCE}/approve").mock(
        return_value=Response(200, json=_instance(status="active", approved_by="ada@example.com"))
    )
    missing = _run("approve", "factory-floor", INSTANCE)
    assert missing.exit_code == 2 and not route.called, (
        "no approval without the fingerprint read on the machine"
    )
    approved = _run("approve", "factory-floor", INSTANCE, "--fingerprint", FINGERPRINT)
    assert approved.exit_code == 0, approved.output
    assert f"instance {INSTANCE} of `factory-floor` is active, approved by ada@example.com" in approved.output


@respx.mock
def test_revoke_instance_says_it_is_final() -> None:
    route = respx.post(f"{ONE}/instances/{INSTANCE}/revoke").mock(
        return_value=Response(200, json=_instance(status="revoked"))
    )
    revoked = _run("revoke-instance", "factory-floor", INSTANCE)
    assert revoked.exit_code == 0, revoked.output
    assert route.called and not route.calls[0].request.content
    assert f"instance {INSTANCE} of `factory-floor` is revoked. This is final." in revoked.output


# ───────────────────────── calls ─────────────────────────


def _call(**changes: Any) -> dict[str, Any]:
    return {
        "id": "call-1",
        "kind": "call_tool",
        "state": "answered",
        "connector": "tracker-at-the-outpost",
        "endpoint": "tracker",
        "tool": "search_issues",
        "write": False,
        "instance_id": INSTANCE,
        "epoch": 0,
        "outcome": "result",
        "is_error": False,
        "refusal": None,
        "arguments_sha256": "a1b2c3d4e5f6" + "0" * 52,
        "result_sha256": "f" * 64,
        "project": "billing-api",
        "run_id": "run-1",
        "action_id": None,
        "created_at": "2026-11-12T10:05:00Z",
        "claimed_at": "2026-11-12T10:05:01Z",
        "finished_at": "2026-11-12T10:05:02Z",
    } | changes


@respx.mock
def test_calls_shows_states_and_refusal_codes_as_digests_only() -> None:
    refused = _call(
        id="call-2", state="refused", tool="create_issue", write=True, outcome=None, refusal="write_denied"
    )
    listing = _call(id="call-3", kind="list_tools", tool=None, connector=None, arguments_sha256=None)
    route = respx.get(f"{ONE}/calls").mock(
        return_value=Response(
            200,
            json={"items": [_call(), refused, listing], "meta": {"next_cursor": "c-2", "has_more": True}},
        )
    )
    shown = _run("calls", "factory-floor", "--state", "refused", "--limit", "3")
    assert shown.exit_code == 0, shown.output
    assert dict(route.calls[0].request.url.params) == {"limit": "3", "state": "refused"}
    rows = [[c.strip() for c in line.split("│")[1:-1]] for line in shown.output.splitlines() if "│" in line]
    assert rows == [
        [
            "2026-11-12T10:05:00Z",
            "search_issues",
            "tracker",
            "tracker-at-the-outpost",
            "answered",
            "result",
            "—",
            "read",
            "a1b2c3d4e5f6",
        ],
        [
            "2026-11-12T10:05:00Z",
            "create_issue",
            "tracker",
            "tracker-at-the-outpost",
            "refused",
            "—",
            "write_denied",
            "write",
            "a1b2c3d4e5f6",
        ],
        ["2026-11-12T10:05:00Z", "(tool listing)", "tracker", "—", "answered", "result", "—", "read", "—"],
    ]
    assert "choregos outposts calls factory-floor --cursor c-2" in shown.output


@respx.mock
def test_calls_reads_the_next_page_from_a_cursor_and_says_nothing_when_there_is_none() -> None:
    route = respx.get(f"{ONE}/calls").mock(
        return_value=Response(200, json={"items": [], "meta": {"next_cursor": None, "has_more": False}})
    )
    shown = _run("calls", "factory-floor", "--cursor", "c-2")
    assert shown.exit_code == 0, shown.output
    assert dict(route.calls[0].request.url.params) == {"limit": "50", "cursor": "c-2"}
    assert "--cursor" not in shown.output


def test_every_command_takes_the_organisation_from_the_option_before_the_profile() -> None:
    with respx.mock:
        elsewhere = respx.get(f"{API}/api/v1/orgs/globex/outposts").mock(
            return_value=Response(200, json={"items": []})
        )
        assert _run("list", "--org", "globex").exit_code == 0
        assert elsewhere.called


def test_without_an_organisation_nothing_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_ORG")
    with respx.mock:
        refused = _run("list")
        assert not respx.calls
    assert refused.exit_code == 1 and "no organisation" in refused.output
