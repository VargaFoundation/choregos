# SPDX-License-Identifier: Apache-2.0
"""`choregos chat` (S67-09, ADR 0154 §2, §8, §9): the workspace, driven by Textual's pilot.

The API is faked with respx, route by route; every payload the fake serves, and every body the workspace
sends to a stop, is validated against `openapi.yaml` (the `contract` fixture).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
V1 = f"{API}/api/v1"
CID = "conv-7"
NOW = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
RUN = "run-a1b2c3d4e5f6a7b8"


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_cli import activity, theme, workspace
    from choregos_cli import client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(theme, "MONOCHROME", False)
    # Elapsed times are read against a pinned clock; a reconnection is not tried within a test.
    monkeypatch.setattr(activity, "clock", lambda: NOW)
    monkeypatch.setattr(workspace, "RECONNECT_S", 3600)
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_TOKEN", "chg_test")
    monkeypatch.setenv("CHOREGOS_ORG", "acme")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _entry(entry_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "kind": "run",
        "id": entry_id,
        "status": "running",
        "project_slug": "billing-api",
        "work_item_id": "wi-12",
        "work_item_key": "FIN-12",
        "workflow": "delivery",
        "stage_role": "implement",
        "agent_slug": "coder",
        "model": "anthropic/sonnet-4.5",
        "initiated_by": "ada@example.com",
        "started_at": _iso(NOW - timedelta(minutes=4, seconds=12)),
        "ended_at": None,
        "last_event": "session/update",
        "steps": 42,
        "spend": {
            "tokens_in": 12_345,
            "tokens_out": 800,
            "tokens_cached": 9_000,
            "cost_usd": 0.84,
            "read_at": _iso(NOW),
            "booked": False,
        },
        "can_stop": True,
    } | changes


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


def _proposal(action_id: str = "act-1") -> dict[str, Any]:
    return {
        "id": action_id,
        "conversation_id": CID,
        "need": "tracker",
        "operation": "create_issue",
        "arguments": {"title": "Close Q3"},
        "summary": "Create the ticket FIN-12",
        "status": "proposed",
        "created_at": "2026-11-12T09:05:00Z",
    }


def _sse(*events: tuple[str, dict[str, Any]]) -> str:
    return "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)


COMPLETED = {
    "id": "resp_1",
    "session_id": CID,
    "status": "completed",
    "output_text": "",
    "usage": {"input_tokens": 1200, "output_tokens": 80},
}


def _delta(text: str) -> tuple[str, dict[str, Any]]:
    return "response.output_text.delta", {
        "type": "response.output_text.delta",
        "response_id": "resp_1",
        "delta": text,
    }


class Platform:
    """Every route the workspace reads, faked; what it sends is kept for the test to look at."""

    def __init__(self, router: respx.Router, contract: Any) -> None:
        self.router, self.contract = router, contract
        self.in_flight = [_entry(RUN)]
        self.finished: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.proposals: list[dict[str, Any]] = []
        self.hold: list[tuple[str, dict[str, Any]]] = []
        self.assistants([{"org": "acme", "slug": "finance", "name": "Finance", "status": "ready"}])
        self.opened = router.post(f"{V1}/me/assistants/acme/finance/conversations").mock(
            return_value=Response(201, json=contract("Conversation", _summary(turns=0, messages=[])))
        )
        router.get(f"{V1}/me/conversations/{CID}").mock(side_effect=self._conversation)
        router.get(f"{V1}/me/conversations/{CID}/actions").mock(side_effect=self._actions)
        router.get(f"{V1}/me/conversations").mock(
            return_value=Response(
                200,
                json=contract(
                    "ConversationPage",
                    {
                        "items": [_summary(), _summary(id="conv-8", title="Hiring plan", assistant="legal")],
                        "meta": {"has_more": False},
                    },
                ),
            )
        )
        router.get(f"{V1}/me/inbox").mock(
            return_value=Response(
                200,
                json=contract(
                    "InboxList",
                    {
                        "items": [
                            {
                                "id": "art-1",
                                "task": "weekly-digest",
                                "installation": "finance",
                                "work_item_id": "wi-3",
                                "name": "digest.md",
                                "version": 2,
                                "type": "document",
                                "media_type": "text/markdown",
                                "created_at": "2026-11-16T07:00:00Z",
                            }
                        ]
                    },
                ),
            )
        )
        self.spend(1.2345)
        self.pending([])
        router.get(f"{V1}/orgs/acme/activity").mock(side_effect=self._listing)
        router.get(f"{V1}/orgs/acme/activity/stream").mock(side_effect=self._stream)
        self.sent_turns: list[dict[str, Any]] = []
        self.turn(
            _sse(_delta("Hello, "), _delta("Quarter close is on track."), ("response.completed", COMPLETED))
        )
        self.stops: list[tuple[str, Any]] = []
        router.post(f"{V1}/runs/{RUN}/cancel").mock(side_effect=self._stop)
        router.post(f"{V1}/work-items/wi-12/actions").mock(side_effect=self._stop)

    def assistants(self, items: list[dict[str, Any]]) -> None:
        body = self.contract("MyAssistantList", {"items": items})
        self.router.get(f"{V1}/me/assistants").mock(return_value=Response(200, json=body))

    def spend(self, cost: float | None) -> None:
        body = {"group_by": "day", "rows": [], "total": {"cost_usd": cost, "cost_eur": 0.0}}
        status = 200 if cost is not None else 403
        self.router.get(f"{V1}/orgs/acme/costs").mock(
            return_value=Response(
                status, json=self.contract("CostReport", body) if cost is not None else {"detail": "no"}
            )
        )

    def pending(self, items: list[dict[str, Any]] | None) -> None:
        """`GET /me/pending`: what waits for the person (None: the route is refused)."""
        if items is None:
            self.router.get(f"{V1}/me/pending").mock(return_value=Response(403, json={"detail": "no"}))
            return
        body = self.contract("PendingList", {"items": items})
        self.router.get(f"{V1}/me/pending").mock(return_value=Response(200, json=body))

    def turn(self, body: str | AsyncIterator[bytes]) -> None:
        if isinstance(body, str):
            for name, data in _events(body):
                if name == "response.output_text.delta":
                    self.contract("AgentDelta", data)
                elif name.startswith("response."):
                    self.contract("AgentResponse", data)
            response = Response(200, text=body, headers={"content-type": "text/event-stream"})
        else:
            response = Response(200, stream=body, headers={"content-type": "text/event-stream"})  # type: ignore[arg-type]
        self.router.post(f"{V1}/me/conversations/{CID}/messages").mock(side_effect=self._answer(response))

    def _answer(self, response: Response) -> Callable[[Any], Response]:
        def answer(request: Any) -> Response:
            self.sent_turns.append(json.loads(request.content))
            return response

        return answer

    def _conversation(self, request: Any) -> Response:
        return Response(200, json=self.contract("Conversation", _summary(messages=self.messages)))

    def _actions(self, request: Any) -> Response:
        return Response(200, json=self.contract("ConversationActionList", {"items": self.proposals}))

    def _listing(self, request: Any) -> Response:
        body = {"in_flight": self.in_flight, "finished": self.finished}
        return Response(200, json=self.contract("ActivityList", body))

    def _stream(self, request: Any) -> Response:
        async def body() -> AsyncIterator[bytes]:
            for n, (name, entry) in enumerate(self.hold):
                self.contract("ActivityEntry", entry)
                yield f"id: {n}\nevent: {name}\ndata: {json.dumps(entry)}\n\n".encode()
            await asyncio.sleep(3600)  # a live stream stays open

        return Response(200, stream=body(), headers={"content-type": "text/event-stream"})  # type: ignore[arg-type]

    def _stop(self, request: Any) -> Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/cancel"):
            self.contract("StopRequest", body)
        else:
            self.contract("WorkItemAction", body)
        self.stops.append((request.url.path, body))
        return Response(202, json={})


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


def make_app(**kwargs: Any) -> Any:
    from choregos_cli.client import AsyncClient, Profile
    from choregos_cli.workspace import Workspace

    return Workspace(AsyncClient(Profile(api_url=API, token="chg_test", org="acme")), "acme", **kwargs)


def screen_text(app: Any) -> str:
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


async def until(pilot: Any, condition: Callable[[], bool], what: str) -> None:
    for _ in range(100):
        if condition():
            await pilot.pause()
            return
        await pilot.pause(0.05)
    raise AssertionError(f"never happened: {what}\n{screen_text(pilot.app)}")


async def ready(pilot: Any) -> None:
    app = pilot.app
    await until(pilot, lambda: app.live and app.today is not None and bool(app.assistants), "loaded")


def node_with(app: Any, text: str) -> Any:
    return next(node for node in app._by_key.values() if text in node.label.plain)


async def put_cursor_on(pilot: Any, text: str) -> None:
    from choregos_cli.workspace import FlightTree

    tree = pilot.app.query_one(FlightTree)
    tree.focus()
    tree.move_cursor(node_with(pilot.app, text), animate=False)
    await pilot.pause()


# ───────────────────────────────── the five parts ─────────────────────────────────


async def test_the_workspace_shows_its_five_parts(platform: Platform) -> None:
    platform.pending([_pending_action()])
    platform.finished = [_entry("run-done", status="succeeded", ended_at=_iso(NOW), work_item_key="FIN-9")]
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: bool(app.pending), "decisions")
        text = screen_text(app)
    assert "Conversation" in text and "Say something" in text
    assert "billing-api" in text and "FIN-12" in text and "● running" in text
    assert "implement · coder" in text and "4m12s · 12.3k in / 800 out · $0.84" in text
    assert "Decisions" in text and "⏸ waiting for you  Merge the release train" in text
    assert "F2 decides one" in text
    assert "digest.md · weekly-digest v2" in text and "Quarter close · finance" in text
    assert "acme · 1 running · 1 waiting for you · $1.23 today" in text
    assert "Ended in the last hour" in text


def _pending_action() -> dict[str, Any]:
    return {
        "kind": "governed_action",
        "id": "gov-1",
        "what": "Merge the release train",
        "where": {"org": "acme", "project_slug": "billing-api"},
        "since": "2026-11-16T09:00:00Z",
        "decision": {
            "method": "POST",
            "path": "/projects/p-1/actions/gov-1/decision",
            "url": "http://console.test/p/billing-api/actions/gov-1",
        },
    }


def _pending_proposal() -> dict[str, Any]:
    return {
        "kind": "conversation_action",
        "id": "act-1",
        "what": "Create the ticket FIN-12",
        "where": {"org": "acme", "conversation_id": CID},
        "since": "2026-11-12T09:05:00Z",
        "decision": {
            "method": "POST",
            "path": f"/me/conversations/{CID}/actions/act-1/confirm",
            "url": f"http://console.test/conversations/{CID}?action=act-1",
        },
    }


async def test_unreadable_pending_actions_and_costs_are_dashes_not_zero(platform: Platform) -> None:
    platform.pending(None)
    platform.spend(None)
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await until(pilot, lambda: app.live and bool(app.assistants), "loaded")
        await until(pilot, lambda: app.pending is None and app.board.in_flight != {}, "decisions")
        await pilot.pause(0.2)
        text = screen_text(app)
    assert "— waiting for you · — today" in text
    assert "not readable right now" in text and "$0.00" not in text


async def test_nothing_waiting_says_so(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await pilot.pause(0.2)
        assert "nothing waits for you" in screen_text(app)


async def test_the_tree_follows_the_activity_stream(platform: Platform) -> None:
    platform.hold = [("activity.upsert", _entry("run-new", work_item_key="FIN-20", stage_role="review"))]
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: "FIN-20" in screen_text(app), "the streamed entry")
        assert "2 running" in screen_text(app)


async def test_expanding_a_run_shows_its_events(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        assert "42 events" not in screen_text(app)
        await put_cursor_on(pilot, "implement")
        await pilot.press("enter")
        await pilot.pause()
        assert "42 events · last: session/update" in screen_text(app)


async def test_every_status_is_a_word(platform: Platform) -> None:
    statuses = ["queued", "running", "succeeded", "failed", "cancelled", "timed_out"]
    platform.in_flight = [
        _entry(f"run-{s}", status=s, work_item_key=f"K-{n}") for n, s in enumerate(statuses)
    ]
    app = make_app()
    async with app.run_test(size=(160, 60)) as pilot:
        await ready(pilot)
        text = screen_text(app)
    for word in ("○ queued", "● running", "✓ done", "✗ failed", "■ cancelled", "✗ timed out"):
        assert word in text, word


# ───────────────────────────────────── a turn ─────────────────────────────────────


async def test_a_turn_is_sent_streamed_and_its_writes_are_listed(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        platform.pending([_pending_proposal()])
        await pilot.press(*"How is the close?", "enter")
        await until(pilot, lambda: "✓ 1200 in / 80 out" in screen_text(app), "the end of the turn")
        text = screen_text(app)
    assert platform.opened.call_count == 1, "the conversation is opened at the first message"
    assert platform.sent_turns == [{"input": "How is the close?", "stream": True}]
    assert "Hello, Quarter close is on track." in text and "you" in text
    assert "⏸ waiting for you  Create the ticket FIN-12" in text
    assert "write proposed by an assistant" in text and "F2 decides one" in text
    assert "$0.0125" in text


async def test_escape_closes_the_stream_so_the_platform_cancels_the_turn(platform: Platform) -> None:
    closed = asyncio.Event()

    async def endless() -> AsyncIterator[bytes]:
        try:
            yield _sse(_delta("Thinking about it"))[:].encode()
            await asyncio.sleep(3600)
        finally:
            closed.set()  # httpx closed the response: the connection is gone, the server cancels (S63-04b)

    platform.turn(endless())
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await pilot.press(*"Take your time", "enter")
        await until(pilot, lambda: "Thinking about it" in screen_text(app), "the first delta")
        assert not closed.is_set()
        await pilot.press("escape")
        await asyncio.wait_for(closed.wait(), 5)
        await until(pilot, lambda: not app._turn_running, "the turn ended")
        text = screen_text(app)
    assert "you cancelled the turn" in text and "Thinking about it" in text


async def test_a_refused_turn_is_said_not_swallowed(platform: Platform) -> None:
    platform.turn(_sse(("error", {"status": 429, "detail": "the conversation's cap is reached"})))
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await pilot.press(*"hello", "enter")
        await until(pilot, lambda: "cap is reached" in screen_text(app), "the refusal")


async def test_several_ready_assistants_ask_for_a_choice_before_a_turn(platform: Platform) -> None:
    platform.assistants(
        [
            {"org": "acme", "slug": "finance", "name": "Finance", "status": "ready"},
            {"org": "acme", "slug": "legal", "name": "Legal", "status": "ready"},
            {"org": "acme", "slug": "draft", "name": "Draft", "status": "incomplete"},
        ]
    )
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        assert "Ctrl+A chooses one" in screen_text(app)
        await pilot.press(*"hello", "enter")
        await pilot.pause()
        assert "Assistants ready for you" in screen_text(app)
        assert "draft" not in screen_text(app).split("Assistants ready for you")[1].split("Enter chooses")[0]
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.assistant == "legal"
    assert not platform.sent_turns


async def test_a_conversation_is_resumed_from_the_list(platform: Platform) -> None:
    platform.messages = [
        {"role": "user", "content": "Where are we?"},
        {"role": "assistant", "content": "Two tasks are left."},
    ]
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await pilot.press("ctrl+o")
        await pilot.pause()
        assert "Your conversations" in screen_text(app) and "Hiring plan" in screen_text(app)
        await pilot.press("enter")
        await until(pilot, lambda: "Two tasks are left." in screen_text(app), "the resumed messages")
        assert app.conversation_id == CID
        await pilot.press(*"and then?", "enter")
        await until(pilot, lambda: bool(platform.sent_turns), "the next turn")
    assert platform.opened.call_count == 0, "a resumed conversation is continued, not reopened"


# ───────────────────────────────────── a stop ─────────────────────────────────────


async def test_a_stop_always_asks_for_a_reason(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await put_cursor_on(pilot, "implement")
        await pilot.press("x")
        await pilot.pause()
        assert "Stop run run-a1b2c3d4" in screen_text(app) and "FIN-12" in screen_text(app)
        await pilot.press("enter")  # no reason
        await pilot.pause()
        assert "a stop needs a reason" in screen_text(app)
        assert platform.stops == []
        await pilot.press(*"wrong branch", "enter")
        await until(pilot, lambda: bool(platform.stops), "the stop")
    assert platform.stops == [(f"/api/v1/runs/{RUN}/cancel", {"reason": "wrong branch"})]


async def test_escape_in_the_stop_dialog_keeps_the_run_running(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await put_cursor_on(pilot, "implement")
        await pilot.press("x", *"x", "escape")
        await pilot.pause()
        assert "Stop run" not in screen_text(app)
    assert platform.stops == []


async def test_a_work_item_is_stopped_with_the_stop_action_and_a_reason(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await put_cursor_on(pilot, "FIN-12")
        await pilot.press("x", *"scope changed", "enter")
        await until(pilot, lambda: bool(platform.stops), "the stop")
    assert platform.stops == [
        ("/api/v1/work-items/wi-12/actions", {"action": "stop", "reason": "scope changed"})
    ]


async def test_a_run_the_person_may_not_stop_opens_no_dialog(platform: Platform) -> None:
    platform.in_flight = [_entry(RUN, can_stop=False)]
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await put_cursor_on(pilot, "implement")
        await pilot.press("x")
        await pilot.pause()
        assert "Stop run" not in screen_text(app)


# ─────────────────────────────── snapshots (SVG) ───────────────────────────────


def _showcase(platform: Platform) -> None:
    """One of everything: a queued, a running, a failed and an ended run, a proposal, a pending action."""
    platform.in_flight = [
        _entry(RUN),
        _entry("run-queued-0001", status="queued", work_item_key="FIN-13", stage_role="review", spend=None),
        _entry(
            "ses-support-01",
            kind="session",
            project_slug=None,
            agent_slug="support",
            status="open",
            spend=None,
        ),
    ]
    platform.finished = [
        _entry(
            "run-failed-0001",
            status="failed",
            work_item_key="OPS-4",
            project_slug="platform",
            ended_at=_iso(NOW),
        ),
        _entry(
            "run-done-00001",
            status="succeeded",
            work_item_key="OPS-3",
            project_slug="platform",
            ended_at=_iso(NOW),
        ),
    ]
    platform.messages = [
        {"role": "user", "content": "Where are we on the quarter close?"},
        {
            "role": "assistant",
            "content": "Two of the three tasks are done:\n\n- reconciliation of the ledgers\n- accruals\n\n"
            "The sign-off waits for you, I proposed the ticket.",
        },
    ]
    platform.proposals = [_proposal()]
    platform.pending([_pending_proposal(), _pending_action()])


SIZES = {"80": (80, 36), "160": (160, 44)}


@pytest.mark.parametrize("colour", ["colour", "no-color"])
@pytest.mark.parametrize("columns", ["80", "160"])
def test_the_workspace_snapshot(
    snap_compare: Any,
    platform: Platform,
    monkeypatch: pytest.MonkeyPatch,
    colour: str,
    columns: str,
) -> None:
    if colour == "no-color":
        monkeypatch.setenv("NO_COLOR", "1")
    _showcase(platform)
    app = make_app(conversation=CID)

    async def settled(pilot: Any) -> None:
        await until(pilot, lambda: app.live and "Quarter close" in screen_text(app), "the workspace")
        await until(pilot, lambda: bool(app.pending), "the decisions")
        await pilot.pause(0.3)

    assert snap_compare(app, terminal_size=SIZES[columns], run_before=settled)


# ─────────────────────────── the entry points (ADR 0154 §3, §8) ───────────────────────────


def test_a_bare_choregos_piped_prints_the_help() -> None:
    from choregos_cli.main import app

    result = CliRunner().invoke(app, [])  # the runner's stdin and stdout are not terminals
    assert result.exit_code == 0, result.output
    assert (
        "Usage:" in result.output
        and "COMMAND" in result.output
        and "activity" in result.output
        and "chat" in result.output
    )


def test_a_bare_choregos_in_a_terminal_opens_the_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_cli import chat
    from choregos_cli.main import app

    opened: list[str] = []
    monkeypatch.setattr(chat, "interactive", lambda: True)
    monkeypatch.setattr(chat, "open_workspace", lambda **kw: opened.append("workspace"))
    assert CliRunner().invoke(app, []).exit_code == 0
    assert opened == ["workspace"]
    opened.clear()
    assert CliRunner().invoke(app, ["activity", "--help"]).exit_code == 0
    assert opened == [], "a command is never replaced by the workspace"


def test_interactive_needs_both_standard_input_and_output_to_be_terminals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_cli import chat

    class Stream:
        def __init__(self, tty: bool) -> None:
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    for stdin, stdout, expected in [(True, True, True), (True, False, False), (False, True, False)]:
        monkeypatch.setattr(sys, "stdin", Stream(stdin))
        monkeypatch.setattr(sys, "stdout", Stream(stdout))
        assert chat.interactive() is expected


def test_chat_refuses_to_run_without_a_terminal() -> None:
    from choregos_cli.main import app

    result = CliRunner().invoke(app, ["chat"])
    assert result.exit_code == 1 and "needs a terminal" in result.output


def test_version_and_the_other_commands_import_no_textual_module() -> None:
    code = (
        "import sys\n"
        "from choregos_cli.main import app\n"
        "from typer.testing import CliRunner\n"
        "for words in (['--version'], ['activity', '--help'], ['ask', '--help']):\n"
        "    assert CliRunner().invoke(app, words).exit_code == 0\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'textual'))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]", done.stdout


def test_the_workspace_module_is_the_only_one_importing_textual() -> None:
    source = Path(__file__).parents[1] / "src" / "choregos_cli"
    importing = sorted(
        path.name
        for path in source.glob("*.py")
        if any(line.startswith(("import textual", "from textual")) for line in path.read_text().splitlines())
    )
    assert importing == ["workspace.py"]
