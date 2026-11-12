# SPDX-License-Identifier: Apache-2.0
"""`choregos ask` (S67-01, ADR 0154 §3): one turn of a durable conversation, streamed and scriptable.

The API is faked with respx, route by route; every payload the fake serves is validated against `openapi.yaml`
(the `contract` fixture), so the fake answers what the platform answers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
V1 = f"{API}/api/v1"
CID = "conv-7"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_cli import client as client_module
    from choregos_cli import theme

    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(theme, "MONOCHROME", False)
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_TOKEN", "chg_test")
    monkeypatch.setenv("CHOREGOS_ORG", "acme")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def _run(*words: str, stdin: str | None = None, env: dict[str, str] | None = None) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, ["ask", *words], input=stdin, env=env)


def _assistant(slug: str, status: str = "ready", org: str = "acme") -> dict[str, Any]:
    return {"org": org, "slug": slug, "name": slug, "status": status}


def _summary(**changes: Any) -> dict[str, Any]:
    return {
        "id": CID,
        "title": "Quarter close",
        "agent": "finance",
        "agent_version": 3,
        "org": "acme",
        "assistant": "finance",
        "status": "open",
        "turns": 1,
        "cost_usd": 0.0125,
        "created_at": "2026-11-12T09:00:00Z",
        "last_activity_at": "2026-11-12T09:05:00Z",
    } | changes


def _action(
    action_id: str, status: str = "proposed", summary: str = "Create the ticket FIN-12"
) -> dict[str, Any]:
    return {
        "id": action_id,
        "conversation_id": CID,
        "need": "tracker",
        "operation": "create_issue",
        "arguments": {"title": "Close Q3"},
        "summary": summary,
        "status": status,
        "created_at": "2026-11-12T09:05:00Z",
    }


def _sse(*events: tuple[str, dict[str, Any]]) -> str:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)


def _completed(text: str = "", status: str = "completed", **extra: Any) -> dict[str, Any]:
    return {
        "id": "resp_1",
        "session_id": CID,
        "status": status,
        "output_text": text,
        "usage": {"input_tokens": 1200, "output_tokens": 80},
    } | extra


def _delta(text: str) -> dict[str, Any]:
    return {"type": "response.output_text.delta", "response_id": "resp_1", "delta": text}


class Platform:
    """The routes a turn calls, faked, each payload checked against the contract."""

    def __init__(self, router: respx.Router, contract: Any) -> None:
        self.router, self.contract = router, contract
        self.assistants([_assistant("finance")])
        self.opened = router.post(f"{V1}/me/assistants/acme/finance/conversations").mock(
            return_value=Response(201, json=contract("Conversation", _summary(turns=0, cost_usd=0.0)))
        )
        router.get(f"{V1}/me/conversations/{CID}").mock(
            return_value=Response(200, json=contract("Conversation", _summary(messages=[])))
        )
        self.actions_seen: list[list[dict[str, Any]]] = [[]]
        router.get(f"{V1}/me/conversations/{CID}/actions").mock(side_effect=self._actions)
        self.turn(_sse(("response.output_text.delta", _delta("Hello")), ("response.completed", _completed())))

    def assistants(self, items: list[dict[str, Any]]) -> None:
        body = self.contract("MyAssistantList", {"items": items})
        self.router.get(f"{V1}/me/assistants").mock(return_value=Response(200, json=body))

    def turn(self, body: str, status: int = 200) -> None:
        for name, data in _events(body):
            schema = {"response.output_text.delta": "AgentDelta"}.get(name)
            if name in ("response.completed", "response.failed", "response.created"):
                schema = "AgentResponse"
            if schema:
                self.contract(schema, data)
        self.sent = self.router.post(f"{V1}/me/conversations/{CID}/messages").mock(
            return_value=Response(status, text=body, headers={"content-type": "text/event-stream"})
        )

    def actions(self, *calls: list[dict[str, Any]]) -> None:
        """What `GET …/actions` answers, call after call (the last one repeats)."""
        self.actions_seen = list(calls)

    def _actions(self, request: Any) -> Response:
        items = self.actions_seen.pop(0) if len(self.actions_seen) > 1 else self.actions_seen[0]
        return Response(200, json=self.contract("ConversationActionList", {"items": items}))


def _events(body: str) -> list[tuple[str, dict[str, Any]]]:
    found = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        found.append((lines["event"], json.loads(lines["data"])))
    return found


@pytest.fixture
def platform(contract: Any) -> Any:
    with respx.mock(assert_all_called=False) as router:
        yield Platform(router, contract)


def test_the_answer_streams_to_stdout_in_order_and_alone(platform: Platform) -> None:
    platform.turn(
        _sse(
            ("response.created", _completed()),
            ("response.output_text.delta", _delta("The quarter ")),
            ("response.output_text.delta", _delta("closes on ")),
            ("response.output_text.delta", _delta("the 30th.")),
            ("response.completed", _completed("The quarter closes on the 30th.")),
        )
    )
    result = _run("When does the quarter close?")
    assert result.exit_code == 0, result.output
    assert result.stdout == "The quarter closes on the 30th.\n"
    assert f"conversation {CID}" in result.stderr
    assert "1200 in / 80 out tokens · $0.0125 spent in this conversation" in result.stderr
    sent = json.loads(platform.sent.calls.last.request.content)
    assert sent == {"input": "When does the quarter close?", "stream": True}
    assert platform.sent.calls.last.request.headers["accept"] == "text/event-stream"


def test_an_answer_not_streamed_is_printed_whole(platform: Platform) -> None:
    platform.turn(_sse(("response.completed", _completed("Whole answer"))))
    result = _run("hi")
    assert result.stdout == "Whole answer\n"


def test_the_message_is_read_from_standard_input(platform: Platform) -> None:
    result = _run("-", stdin="from a pipe\n")
    assert result.exit_code == 0, result.output
    assert json.loads(platform.sent.calls.last.request.content)["input"] == "from a pipe\n"


def test_an_empty_message_is_refused_before_any_call(platform: Platform) -> None:
    result = _run(stdin="  \n")
    assert result.exit_code == 1
    assert "nothing to ask" in result.output
    assert not platform.sent.called and not platform.opened.called


def test_a_new_turn_opens_a_conversation_of_the_only_ready_assistant(platform: Platform) -> None:
    platform.assistants(
        [_assistant("finance"), _assistant("legal", "incomplete"), _assistant("hr", org="other")]
    )
    result = _run("hi")
    assert result.exit_code == 0, result.output
    assert platform.opened.called


def test_several_ready_assistants_must_be_chosen(platform: Platform) -> None:
    platform.assistants([_assistant("finance"), _assistant("legal")])
    result = _run("hi")
    assert result.exit_code == 1
    assert "choose one with --assistant (finance, legal)" in " ".join(result.output.split())
    assert not platform.opened.called


def test_an_assistant_outside_the_audience_or_not_ready_is_refused(platform: Platform) -> None:
    platform.assistants([_assistant("finance"), _assistant("legal", "incomplete")])
    assert "no assistant `hr`" in _run("hi", "--assistant", "hr").output
    assert "is incomplete" in _run("hi", "--assistant", "legal").output
    assert not platform.opened.called


def test_no_ready_assistant_says_so(platform: Platform) -> None:
    platform.assistants([])
    result = _run("hi")
    assert result.exit_code == 1 and "no assistant is ready for you in acme" in result.output


def test_a_resumed_conversation_opens_nothing(platform: Platform) -> None:
    result = _run("and the next one?", "--conversation", CID)
    assert result.exit_code == 0, result.output
    assert not platform.opened.called
    assert platform.sent.called


def test_continue_picks_the_assistant_s_most_recent_open_conversation(
    platform: Platform, contract: Any
) -> None:
    page = {
        "items": [
            _summary(id="conv-other", assistant="legal"),
            _summary(id="conv-closed", status="closed"),
            _summary(id=CID, status="idle"),
            _summary(id="conv-older"),
        ],
        "meta": {"has_more": False},
    }
    listed = platform.router.get(f"{V1}/me/conversations").mock(
        return_value=Response(200, json=contract("ConversationPage", page))
    )
    result = _run("again", "--continue")
    assert result.exit_code == 0, result.output
    assert listed.called and not platform.opened.called
    assert platform.sent.called


def test_continue_without_a_conversation_opens_one(platform: Platform, contract: Any) -> None:
    page = {"items": [_summary(status="closed")], "meta": {"has_more": False}}
    platform.router.get(f"{V1}/me/conversations").mock(
        return_value=Response(200, json=contract("ConversationPage", page))
    )
    assert _run("again", "--continue").exit_code == 0
    assert platform.opened.called


def test_conversation_and_continue_together_are_refused(platform: Platform) -> None:
    result = _run("x", "--conversation", CID, "--continue")
    assert result.exit_code == 1 and "give one" in result.output


def test_a_failed_turn_exits_one_with_its_reason(platform: Platform) -> None:
    error = {"code": "budget_exceeded", "message": "the conversation's cap is spent"}
    platform.turn(_sse(("response.failed", _completed(status="failed", error=error))))
    result = _run("hi")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "the conversation's cap is spent (budget_exceeded)" in result.stderr


def test_an_error_event_exits_one_with_its_detail(platform: Platform) -> None:
    platform.router.post(f"{V1}/me/conversations/{CID}/messages").mock(
        return_value=Response(
            200,
            text='event: error\ndata: {"status": 403, "detail": "you left the audience"}\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )
    result = _run("hi", "--conversation", CID)
    assert result.exit_code == 1
    assert "you left the audience (403)" in result.stderr


def test_a_turn_refused_before_the_stream_is_a_red_line(platform: Platform) -> None:
    platform.router.post(f"{V1}/me/conversations/{CID}/messages").mock(
        return_value=Response(
            409, json={"title": "Conflict", "detail": "another turn of this conversation runs"}
        )
    )
    result = _run("hi", "--conversation", CID)
    assert result.exit_code == 1
    assert "409 — another turn of this conversation runs" in result.output


def test_the_writes_proposed_during_this_turn_only_are_printed_with_their_link(platform: Platform) -> None:
    earlier = _action("act-old", summary="An earlier proposal")
    platform.actions([earlier, _action("act-done", "executed")], [earlier, _action("act-new")])
    result = _run("open the ticket", "--conversation", CID)
    assert result.exit_code == 0, result.output
    assert "waiting for you Create the ticket FIN-12" in " ".join(result.stderr.split())
    assert f"confirm or refuse: {API}/conversations/{CID}?action=act-new" in result.stderr
    assert "An earlier proposal" not in result.stderr
    assert "FIN-12" not in result.stdout


def test_json_prints_one_object_and_nothing_while_the_turn_runs(platform: Platform) -> None:
    platform.actions([_action("act-new"), _action("act-gone", "refused")])
    platform.turn(
        _sse(
            ("response.output_text.delta", _delta("Done, ")),
            ("response.output_text.delta", _delta("proposed.")),
            ("response.completed", _completed("Done, proposed.")),
        )
    )
    result = _run("go", "--json")
    assert result.exit_code == 0, result.output
    document = json.loads(result.stdout)
    assert document == {
        "conversation_id": CID,
        "status": "completed",
        "text": "Done, proposed.",
        "pending_actions": [
            {
                "id": "act-new",
                "summary": "Create the ticket FIN-12",
                "confirm_url": f"{API}/conversations/{CID}?action=act-new",
            }
        ],
        "usage": {"input_tokens": 1200, "output_tokens": 80},
        "error": None,
    }
    assert result.stderr == ""


def test_no_color_writes_no_escape_sequence(platform: Platform) -> None:
    platform.actions([_action("act-new")])
    forced = {"FORCE_COLOR": "1", "TTY_COMPATIBLE": "1"}
    coloured = _run("hi", env=forced)
    assert ANSI.search(coloured.stderr), "the forced colour run must colour, or the test proves nothing"
    plain = _run("hi", env=forced | {"NO_COLOR": "1"})
    assert plain.exit_code == 0
    assert not ANSI.search(plain.stderr) and not ANSI.search(plain.stdout)
    assert "! waiting for you" in plain.stderr or "⏸ waiting for you" in plain.stderr


def test_the_no_color_option_turns_colour_off(platform: Platform) -> None:
    from choregos_cli.main import app

    platform.actions([_action("act-new")])
    result = CliRunner().invoke(
        app, ["--no-color", "ask", "hi"], env={"FORCE_COLOR": "1", "TTY_COMPATIBLE": "1"}
    )
    assert result.exit_code == 0, result.output
    assert not ANSI.search(result.stderr)


class _Interrupted:
    """A turn's stream that sends one delta, then the person presses Ctrl-C."""

    def __iter__(self) -> Any:
        yield _sse(("response.output_text.delta", _delta("Half an answer"))).encode()
        raise KeyboardInterrupt

    def close(self) -> None:
        """Nothing to release."""


def test_a_delta_is_written_as_it_comes_and_ctrl_c_leaves_the_turn(platform: Platform) -> None:
    from httpx import SyncByteStream

    class Stream(_Interrupted, SyncByteStream):
        pass

    platform.router.post(f"{V1}/me/conversations/{CID}/messages").mock(
        return_value=Response(200, stream=Stream(), headers={"content-type": "text/event-stream"})
    )
    result = _run("hi", "--conversation", CID)
    assert result.exit_code == 130
    assert result.stdout == "Half an answer"
    assert "the platform cancels the turn" in result.stderr


def test_an_unreachable_api_during_the_turn_is_a_red_line(platform: Platform) -> None:
    import httpx

    platform.router.post(f"{V1}/me/conversations/{CID}/messages").mock(
        side_effect=httpx.ConnectError("refused")
    )
    result = _run("hi", "--conversation", CID)
    assert result.exit_code == 1
    assert f"cannot reach {API}: refused" in " ".join(result.output.split())
