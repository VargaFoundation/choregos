# SPDX-License-Identifier: Apache-2.0
"""`choregos chat` (S67-09, ADR 0154 §2, §8, §9): the workspace, driven by Textual's pilot.

The API is faked with respx, route by route; every payload the fake serves, and every body the workspace
sends to a stop, is validated against `openapi.yaml` (the `contract` fixture).
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from textual.binding import Binding
from typer.testing import CliRunner

API = "http://api.test"
V1 = f"{API}/api/v1"
CID = "conv-7"
NOW = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)
RUN = "run-a1b2c3d4e5f6a7b8"


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tranibor_cli import activity, theme, workspace
    from tranibor_cli import client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(theme, "MONOCHROME", False)
    # Elapsed times are read against a pinned clock; a reconnection is not tried within a test.
    monkeypatch.setattr(activity, "clock", lambda: NOW)
    monkeypatch.setattr(workspace, "RECONNECT_S", 3600)
    monkeypatch.setattr(workspace, "RUN_POLL_S", 0.05)
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


def _event(seq: int, update: dict[str, Any], seconds_ago: int) -> dict[str, Any]:
    return {
        "seq": seq,
        "type": "session/update",
        "ts": _iso(NOW - timedelta(seconds=seconds_ago)),
        "payload": {"sessionId": "sess-1", "update": update},
    }


def _call(call_id: str, title: str, kind: str, status: str = "pending", **extra: Any) -> dict[str, Any]:
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": call_id,
        "title": title,
        "kind": kind,
        "status": status,
    } | extra


def _update(call_id: str, status: str) -> dict[str, Any]:
    return {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": status}


def _journal() -> list[dict[str, Any]]:
    """A claude-code journal, timed against the pinned clock: a plan, a finished read, a failed command and
    a call still running. The `_meta` a backend adds is there to be ignored (ADR 0154 §5)."""
    plan = {
        "sessionUpdate": "plan",
        "entries": [
            {"content": "Read the totals code", "status": "completed", "priority": "high"},
            {"content": "Run the tests", "status": "in_progress", "priority": "medium"},
        ],
    }
    spy = {"_meta": {"claudeCode": {"toolName": "Task", "parentToolUseId": "toolu_zzz"}}}
    updates = [
        (60, plan),
        (50, _call("toolu_01", "Read src/invoices/total.py", "read", **spy)),
        (48, _update("toolu_01", "completed")),
        (30, _call("toolu_02", "`make test`", "execute")),
        (25, _update("toolu_02", "failed")),
        (12, _call("toolu_03", "`make test` again", "execute", "in_progress")),
    ]
    return [_event(n, update, ago) for n, (ago, update) in enumerate(updates, start=1)]


def _agent_node(node_id: str, parent: str | None, kind: str, **changes: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "parent_id": parent,
        "kind": kind,
        "tool": "Agent" if kind == "subagent" else None,
        "status": "running",
        "started_at": _iso(NOW - timedelta(minutes=1, seconds=4)),
        "ended_at": None,
        "tool_calls": 0,
    } | changes


def _project(pid: str, slug: str, name: str, *, active: int, month: float) -> dict[str, Any]:
    return {
        "id": pid,
        "slug": slug,
        "org": "acme",
        "name": name,
        "status": "active",
        "config": {},
        "workflow_name": "delivery",
        "stats": {"active_work_items": active, "cost_month_eur": month, "budget_month_eur": 100.0},
    }


def _work_item(wid: str, key: str, title: str, state: str, cost: float) -> dict[str, Any]:
    return {
        "id": wid,
        "project_slug": "billing-api",
        "tracker_key": key,
        "title": title,
        "state": state,
        "state_display": state.replace("_", " "),
        "size": "M",
        "totals": {"cost_eur": cost},
        "pr_url": None,
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
        self.currency("USD")
        self.pending([])
        router.get(f"{V1}/orgs/acme/activity").mock(side_effect=self._listing)
        router.get(f"{V1}/orgs/acme/activity/stream").mock(side_effect=self._stream)
        self.sent_turns: list[dict[str, Any]] = []
        self.turn(
            _sse(_delta("Hello, "), _delta("Quarter close is on track."), ("response.completed", COMPLETED))
        )
        self.stops: list[tuple[str, Any]] = []
        router.post(f"{V1}/runs/{RUN}/cancel").mock(side_effect=self._stop)
        # What an expanded run node reads (S67-14): its journal, its context and its sub-agents.
        self.journal: list[dict[str, Any]] = _journal()
        self.breakdown: dict[str, Any] | None = None
        self.agent_nodes: list[dict[str, Any]] = [_agent_node(RUN, None, "agent")]
        self.asked: list[tuple[str, str]] = []  # (route, after_seq or "")
        self.journal_status = 200
        router.get(url__regex=rf"{re.escape(V1)}/runs/[^/]+/events").mock(side_effect=self._events)
        router.get(url__regex=rf"{re.escape(V1)}/runs/[^/]+/context-breakdown").mock(
            side_effect=self._context
        )
        router.get(url__regex=rf"{re.escape(V1)}/runs/[^/]+/agents").mock(side_effect=self._agents)
        router.post(f"{V1}/work-items/wi-12/actions").mock(side_effect=self._stop)
        # The Projects tab (S67-23): every readable project, a project's items, an item's runs.
        self.projects: list[dict[str, Any]] = [
            _project("p-1", "billing-api", "Billing API", active=2, month=41.5),
            _project("p-2", "docs-site", "Docs site", active=0, month=0.0),
        ]
        self.projects_status = 200
        router.get(f"{V1}/orgs/acme/projects").mock(side_effect=self._projects)
        router.get(f"{V1}/projects/p-1/work-items").mock(side_effect=self._items)
        router.get(f"{V1}/projects/p-2/work-items").mock(side_effect=self._items)
        router.get(url__regex=rf"{re.escape(V1)}/work-items/[^/]+/runs").mock(side_effect=self._item_runs)

    def _projects(self, request: Any) -> Response:
        if self.projects_status != 200:
            return Response(self.projects_status, json={"title": "Forbidden", "detail": "no"})
        # `Project.config` refers to a schema file the `contract` fixture cannot resolve: the part the
        # workspace reads (`stats`) is validated, the rest is the fixture's limit.
        for project in self.projects:
            self.contract("ProjectStats", project["stats"])
        return Response(200, json={"items": self.projects, "meta": {"has_more": False}})

    def _items(self, request: Any) -> Response:
        if request.url.path.endswith("/p-2/work-items"):
            return Response(
                200, json=self.contract("WorkItemPage", {"items": [], "meta": {"has_more": False}})
            )
        items = [
            _work_item("wi-12", "FIN-12", "Fix the totals rounding", "in_progress", 12.5),
            _work_item("wi-9", "FIN-9", "Add the VAT column", "done", 3.0),
        ]
        return Response(
            200, json=self.contract("WorkItemPage", {"items": items, "meta": {"has_more": False}})
        )

    def _item_runs(self, request: Any) -> Response:
        runs = [
            {
                "id": RUN,
                "status": "running",
                "stage_role": "implement",
                "attempt": 2,
                "backend": "claude-code",
                "model": "anthropic/sonnet-4.5",
                "cost_usd": 0.84,
                "started_at": _iso(NOW - timedelta(minutes=4, seconds=12)),
            }
        ]
        return Response(200, json=[self.contract("RunSummary", r) for r in runs])

    def assistants(self, items: list[dict[str, Any]]) -> None:
        body = self.contract("MyAssistantList", {"items": items})
        self.router.get(f"{V1}/me/assistants").mock(return_value=Response(200, json=body))

    def spend(self, cost: float | None) -> None:
        body = {
            "group_by": "day",
            "rows": [],
            "total": {"cost_usd": cost, "cost_eur": None if cost is None else cost * 0.9},
        }
        status = 200 if cost is not None else 403
        self.router.get(f"{V1}/orgs/acme/costs").mock(
            return_value=Response(
                status, json=self.contract("CostReport", body) if cost is not None else {"detail": "no"}
            )
        )

    def currency(self, code: str | None) -> None:
        """`GET /orgs`: the organisation's currency (None: the route is refused)."""
        if code is None:
            self.router.get(f"{V1}/orgs").mock(return_value=Response(403, json={"detail": "no"}))
            return
        org = {"slug": "acme", "name": "Acme", "currency": code}
        self.router.get(f"{V1}/orgs").mock(return_value=Response(200, json=[org]))

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

    def _events(self, request: Any) -> Response:
        after = int(request.url.params.get("after_seq", 0))
        self.asked.append(("events", str(after)))
        if self.journal_status != 200:
            return Response(self.journal_status, json={"title": "Forbidden", "detail": "no"})
        return Response(200, json=[e for e in self.journal if e["seq"] > after])

    def _context(self, request: Any) -> Response:
        self.asked.append(("context", ""))
        body = {
            "run_id": RUN,
            "measurable": self.breakdown is not None,
            "backend": "claude-code",
            "latest": self.breakdown,
            "series": [],
        }
        return Response(200, json=self.contract("RunContext", body))

    def _agents(self, request: Any) -> Response:
        self.asked.append(("agents", ""))
        return Response(200, json=self.contract("RunAgentList", {"items": self.agent_nodes}))

    def add_events(self, *updates: dict[str, Any], seconds_ago: int = 1) -> None:
        """What the agent journals while the person watches."""
        for update in updates:
            self.journal.append(_event(len(self.journal) + 1, update, seconds_ago))

    def asked_events(self) -> list[str]:
        return [after for route, after in self.asked if route == "events"]

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


@pytest.fixture(autouse=True)
def steady_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """An input's cursor blinks every half second: a snapshot taken on a loaded runner caught it in either
    phase. Held visible, a snapshot is one picture whatever the timing."""
    from textual.widgets import Input

    monkeypatch.setattr(Input, "_toggle_cursor", lambda self: setattr(self, "_cursor_visible", True))


def make_app(**kwargs: Any) -> Any:
    from tranibor_cli.client import AsyncClient, Profile
    from tranibor_cli.workspace import Workspace

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
    from tranibor_cli.workspace import FlightTree

    pilot.app.action_show_view("activity")
    await pilot.pause()
    tree = pilot.app.query_one(FlightTree)
    tree.focus()
    tree.move_cursor(node_with(pilot.app, text), animate=False)
    await pilot.pause()


# ───────────────────────────────── the five parts ─────────────────────────────────


def pane(app: Any) -> str:
    """What the context pane (right of the rule) shows."""
    return "\n".join(line.partition("│")[2].strip() for line in screen_text(app).splitlines())


async def show(pilot: Any, view: str) -> None:
    pilot.app.action_show_view(view)
    await pilot.pause()


async def test_the_workspace_is_one_screen_of_tabs_and_each_tab_shows_its_part(platform: Platform) -> None:
    platform.pending([_pending_action()])
    platform.finished = [_entry("run-done", status="succeeded", ended_at=_iso(NOW), work_item_key="FIN-9")]
    app = make_app(conversation=CID)
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: bool(app.pending) and app.conversation is not None, "loaded")
        home = screen_text(app)
        for number, name in enumerate(
            ["Conversation", "Activity", "Projects", "Decisions", "Artefacts", "Live"], 1
        ):
            assert f"{number} {name}" in home or f"{number}  {name}" in home, name
        assert "Say something" in home
        assert "acme · finance" in home and "1 running" in home and "1 waiting for you" in home
        assert "$1.23 today" in home
        assert app.query_one("#views").current == "view-conversation"
        await show(pilot, "activity")
        text = screen_text(app)
        assert "billing-api" in text and "FIN-12" in text and "● running" in text
        assert "implement · coder" in text and "4m12s · 12.3k in / 800 out · $0.84" in text
        assert "Ended in the last hour" in text
        await show(pilot, "decisions")
        text = screen_text(app)
        assert "Merge the release train" in text and "billing-api" in text
        assert re.search(r"4 +Decisions +1", text)
        await show(pilot, "artefacts")
        text = screen_text(app)
        assert re.search(r"digest\.md +weekly-digest v2", text)
        await show(pilot, "projects")
        assert "Billing API" in screen_text(app)
        await show(pilot, "live")
        assert "coming" in screen_text(app)


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
        head = screen_text(app)
        await show(pilot, "decisions")
        text = screen_text(app)
    assert "— waiting for you" in head and "— today" in head and "$0.00" not in head
    assert "not readable right now" in text


async def test_nothing_waiting_says_so(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await show(pilot, "decisions")
        await pilot.pause(0.2)
        assert "nothing waits for you" in screen_text(app)


async def test_the_tree_follows_the_activity_stream(platform: Platform) -> None:
    platform.hold = [("activity.upsert", _entry("run-new", work_item_key="FIN-20", stage_role="review"))]
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await show(pilot, "activity")
        await until(pilot, lambda: "FIN-20" in screen_text(app), "the streamed entry")
        assert "2 running" in screen_text(app)


async def test_every_status_is_a_word(platform: Platform) -> None:
    statuses = ["queued", "running", "succeeded", "failed", "cancelled", "timed_out"]
    platform.in_flight = [
        _entry(f"run-{s}", status=s, work_item_key=f"K-{n}") for n, s in enumerate(statuses)
    ]
    app = make_app()
    async with app.run_test(size=(160, 60)) as pilot:
        await ready(pilot)
        await show(pilot, "activity")
        text = screen_text(app)
    for word in ("○ queued", "● running", "✓ done", "✗ failed", "■ cancelled", "✗ timed out"):
        assert word in text, word


# ───────────────────────── failures and decisions in one line (S67-22) ─────────────────────────

BUDGET_BODY = (
    '{"error": {"message": "Budget has been exceeded! Current cost: 10.2, Max budget: 10.0",'
    ' "type": "budget_exceeded", "param": null, "code": "400"}}'
)


@pytest.mark.parametrize(
    ("reason", "word"),
    [
        ("loop", "loop"),
        ("budget", "budget spent"),
        ("max_turns", "too many tool calls"),
        ("timeout", "timed out"),
        ("gateway", "gateway refused"),
    ],
)
async def test_a_failed_run_prints_its_reason_as_a_word_and_its_summary_in_the_pane(
    platform: Platform, reason: str, word: str
) -> None:
    failure = {"reason": reason, "summary": "The run stopped at the 31st call."}
    platform.finished = [_entry("run-bad", status="failed", ended_at=_iso(NOW), failure=failure)]
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await put_cursor_on(pilot, "failed")
        await until(
            pilot, lambda: "The run stopped at the 31st call." in pane(app), "the summary in the pane"
        )
        tree = "\n".join(line.partition("│")[0] for line in screen_text(app).splitlines())
        assert "✗ failed" in tree and f"· {word}" in tree
        assert "The run stopped" not in tree
        assert word in pane(app)


async def test_a_provider_error_body_never_reaches_a_list(platform: Platform) -> None:
    failure = {"reason": BUDGET_BODY, "summary": "Budget exceeded."}
    platform.finished = [_entry("run-bad", status="failed", ended_at=_iso(NOW), failure=failure)]
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await show(pilot, "activity")
        text = screen_text(app)
    assert "✗ failed" in text and "{" not in text and "budget_exceeded" not in text


async def test_a_long_decision_title_is_one_line_and_whole_in_the_pane(platform: Platform) -> None:
    title = "Merge the release train\nit carries the ledger fix and 14 other changes " + "x" * 200
    platform.pending([{**_pending_action(), "what": title}])
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: bool(app.pending), "the decisions")
        await show(pilot, "decisions")
        rows = [line.partition("│")[0] for line in screen_text(app).splitlines()]
        assert sum("Merge the release train" in row for row in rows) == 1
        assert not any("ledger fix" in row for row in rows)
        assert "ledger fix" in pane(app)


async def test_a_decision_whose_title_starts_with_a_blank_line_is_not_a_blank_row(platform: Platform) -> None:
    platform.pending([{**_pending_action(), "what": "\n\nMerge the release train"}])
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: bool(app.pending), "the decisions")
        await show(pilot, "decisions")
        rows = [line.partition("│")[0] for line in screen_text(app).splitlines()]
        assert any("Merge the release train" in row for row in rows)


# ─────────────────────────────── the tabs (S67-21) ───────────────────────────────


async def test_digits_tab_and_shift_tab_move_between_the_tabs(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        assert app.current_view == "conversation"
        await pilot.press("tab")
        assert app.current_view == "activity"
        await pilot.press("shift+tab", "shift+tab")
        assert app.current_view == "live", "the tabs wrap around"
        await pilot.press("tab")
        assert app.current_view == "conversation"
        app.query_one("#tree").focus()
        await pilot.press("4")
        assert app.current_view == "decisions"
        await pilot.press("2")
        assert app.current_view == "activity"


async def test_a_digit_typed_in_the_composer_is_a_character_not_a_tab(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await pilot.press("3", "4")
        assert app.current_view == "conversation"
        assert app.query_one("#prompt").value == "34"


async def test_a_tab_is_chosen_with_a_click_on_its_name(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        row = next(
            line for line in screen_text(app).splitlines() if "3 Projects" in line or "3  Projects" in line
        )
        await pilot.click("#tabs", offset=(row.index("Artefacts") + 2, 0))
        assert app.current_view == "artefacts"


async def test_the_tab_keys_do_nothing_over_a_dialog(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await put_cursor_on(pilot, "implement")
        await pilot.press("x")
        await pilot.pause()
        assert "Stop run" in screen_text(app)
        await pilot.press("tab")
        assert app.current_view == "activity", "Tab moves the dialog's focus, not the workspace's tab"
        assert "Stop run" in screen_text(app)


async def test_the_header_counts_what_runs_and_what_waits(platform: Platform) -> None:
    platform.pending([_pending_action(), _pending_proposal()])
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: bool(app.pending), "the decisions")
        head = screen_text(app).splitlines()[1]
    assert "acme" in head and "finance" in head
    assert "1 running" in head and "2 waiting for you" in head and "$1.23 today" in head


async def test_the_composer_is_one_line_with_the_cost_of_the_turn_at_its_right(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await pilot.press(*"How is the close?", "enter")
        await until(pilot, lambda: "$0.0125" in str(app.query_one("#cost").render()), "the cost")
        line = next(line for line in screen_text(app).splitlines() if "›" in line)
    main = line.split("│")[0]
    assert main.strip().startswith("›") and main.rstrip().endswith("$0.0125")


async def test_the_context_pane_follows_the_selection(platform: Platform) -> None:
    platform.in_flight = [_entry(RUN), _entry("run-queued-0001", status="queued", work_item_key="FIN-13")]
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await put_cursor_on(pilot, "implement")
        await until(pilot, lambda: "run-a1b2c3d4" in pane(app), "the run in the pane")
        body = pane(app)
        assert "FIN-12" in body and "coder" in body
        await put_cursor_on(pilot, "FIN-13")
        await pilot.pause()
        assert pane(app) != body and "run-a1b2c3d4" not in pane(app)


async def test_the_decisions_context_shows_what_the_highlighted_one_is(platform: Platform) -> None:
    platform.pending([_pending_action(), _pending_proposal()])
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: bool(app.pending), "the decisions")
        await show(pilot, "decisions")
        first = pane(app)
        await pilot.press("down")
        await pilot.pause()
        assert pane(app) != first


@pytest.mark.parametrize(("columns", "shown"), [(160, True), (120, True), (119, False), (80, False)])
async def test_the_context_pane_folds_below_120_columns(
    platform: Platform, columns: int, shown: bool
) -> None:
    app = make_app()
    async with app.run_test(size=(columns, 40)) as pilot:
        await ready(pilot)
        await pilot.pause()
        assert app.query_one("#context").display is shown


# ───────────────────────────────────── a turn ─────────────────────────────────────


async def test_a_turn_is_sent_streamed_and_its_writes_are_listed(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        platform.pending([_pending_proposal()])
        await pilot.press(*"How is the close?", "enter")
        await until(pilot, lambda: "✓ 1200 in / 80 out" in screen_text(app), "the end of the turn")
        text = screen_text(app)
        await show(pilot, "decisions")
        decisions = screen_text(app)
    assert platform.opened.call_count == 1, "the conversation is opened at the first message"
    assert platform.sent_turns == [{"input": "How is the close?", "stream": True}]
    assert "Hello, Quarter close is on track." in text and "you" in text
    assert "$0.0125" in text and re.search(r"4 +Decisions +1", text)
    assert "Create the ticket FIN-12" in decisions and "write proposed by an assistant" in decisions


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


# ─────────────────────── an expanded run node (S67-14, ADR 0154 §2, §5) ───────────────────────


async def expand_run(pilot: Any, text: str = "implement") -> None:
    await put_cursor_on(pilot, text)
    await pilot.press("enter")


def lines_of(app: Any) -> list[str]:
    return [line.rstrip() for line in screen_text(app).splitlines()]


async def test_an_expanded_run_shows_its_plan_its_tool_calls_its_context_and_its_sub_agents(
    platform: Platform,
) -> None:
    platform.agent_nodes = [
        _agent_node(RUN, None, "agent", tool_calls=3),
        _agent_node("toolu_a", RUN, "subagent", tool_calls=2),
        _agent_node("toolu_b", "toolu_a", "subagent", tool="Task", tool_calls=1, status="completed"),
    ]
    app = make_app()
    async with app.run_test(size=(160, 50)) as pilot:
        await ready(pilot)
        assert platform.asked == [], "a collapsed run is not read"
        await expand_run(pilot)
        await until(pilot, lambda: "Plan  1/2 done" in screen_text(app), "the plan")
        lines = lines_of(app)
    text = "\n".join(lines)
    assert "✓ Read the totals code" in text and "● Run the tests" in text
    assert "Steps  3 tool calls · 2 finished" in text
    assert re.search(r"✓ done\s+2s\s+read\s+Read src/invoices/total\.py", text), text
    assert re.search(r"✗ failed\s+5s\s+execute\s+`make test`", text), text
    assert re.search(r"● running\s+12s\s+execute\s+`make test` again", text), text
    assert "Context: not measured" in text
    first = next(n for n, line in enumerate(lines) if "sub-agent Agent · 2 tool calls" in line)
    second = next(n for n, line in enumerate(lines) if "sub-agent Task · 1 tool call" in line)
    assert second == first + 1

    def indent(n: int) -> int:
        found = re.search(r"[●✓] \w+ +sub-agent", lines[n])
        assert found, lines[n]
        return found.start()

    assert indent(second) > indent(first)


async def test_an_expanded_run_shows_the_context_when_it_is_measured(platform: Platform) -> None:
    platform.breakdown = {
        "call": 7,
        "at": _iso(NOW),
        "model": "anthropic/sonnet-4.5",
        "prompt_tokens": 40_000,
        "max_input_tokens": 200_000,
        "sections": {"system": 4_000, "messages": 36_000},
    }
    platform.contract(
        "RunContext",
        {"run_id": RUN, "measurable": True, "backend": "x", "latest": platform.breakdown, "series": []},
    )
    app = make_app()
    async with app.run_test(size=(160, 50)) as pilot:
        await ready(pilot)
        await expand_run(pilot)
        await until(pilot, lambda: "Context: call 7" in screen_text(app), "the context")
        assert "Context: call 7 · sonnet-4.5 · 40.0k of 200.0k (20%)" in screen_text(app)
        assert "not measured" not in screen_text(app)


async def test_an_open_run_is_read_incrementally_while_it_runs(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 50)) as pilot:
        await ready(pilot)
        await expand_run(pilot)
        await until(pilot, lambda: "Steps  3 tool calls" in screen_text(app), "the first read")
        platform.add_events(_update("toolu_03", "completed"), _call("toolu_04", "Edit total.py", "edit"))
        await until(pilot, lambda: "Edit total.py" in screen_text(app), "the later events")
        asked = platform.asked_events()
        assert "Steps  4 tool calls · 3 finished" in screen_text(app)
    assert asked[0] == "0" and "6" in asked, asked  # then only what is after the last event read
    assert asked.count("0") == 1, asked


async def test_collapsing_a_run_stops_reading_it(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 50)) as pilot:
        await ready(pilot)
        await expand_run(pilot)
        await until(pilot, lambda: "Steps  3 tool calls" in screen_text(app), "the first read")
        await pilot.press("enter")  # collapse
        await pilot.pause(0.1)
        asked = len(platform.asked)
        await pilot.pause(0.5)  # ten poll periods
        assert len(platform.asked) == asked, "a collapsed run is still being read"
        assert "Steps  3 tool calls" not in screen_text(app)
        await pilot.press("enter")  # open again: it resumes after the last event, not from zero
        await until(pilot, lambda: len(platform.asked) > asked, "reading again")
        assert platform.asked_events()[-1] == "6"


async def test_a_run_that_has_ended_is_read_once(platform: Platform) -> None:
    platform.in_flight = []
    platform.finished = [_entry(RUN, status="succeeded", ended_at=_iso(NOW))]
    app = make_app()
    async with app.run_test(size=(160, 50)) as pilot:
        await ready(pilot)
        await expand_run(pilot)
        await until(pilot, lambda: "Steps  3 tool calls" in screen_text(app), "the read")
        await pilot.pause(0.5)
        assert platform.asked_events() == ["0"]


async def test_a_run_stays_open_when_it_moves_to_the_ended_part(platform: Platform) -> None:
    from tranibor_cli.activity import Board

    app = make_app()
    async with app.run_test(size=(160, 50)) as pilot:
        await ready(pilot)
        await expand_run(pilot)
        await until(pilot, lambda: "Steps  3 tool calls" in screen_text(app), "the read")
        platform.in_flight, platform.finished = [], [_entry(RUN, status="succeeded", ended_at=_iso(NOW))]
        app.board = Board.of({"in_flight": [], "finished": platform.finished})
        app.redraw()
        await pilot.pause(0.2)
        assert "Steps  3 tool calls" in screen_text(app)


async def test_an_unreadable_journal_is_said_on_the_node_and_the_workspace_goes_on(
    platform: Platform,
) -> None:
    platform.journal_status = 403
    app = make_app()
    async with app.run_test(size=(160, 50)) as pilot:
        await ready(pilot)
        await expand_run(pilot)
        await until(pilot, lambda: "the journal cannot be read (403)" in screen_text(app), "the notice")
        assert "FIN-12" in screen_text(app)


async def test_what_a_backend_writes_in_meta_does_not_move_a_tool_call(platform: Platform) -> None:
    """The journal's first call carries a `_meta` naming a parent: the platform's agent list says there is
    no sub-agent, so there is none, and the call stays flat under the run."""
    app = make_app()
    async with app.run_test(size=(160, 50)) as pilot:
        await ready(pilot)
        await expand_run(pilot)
        await until(pilot, lambda: "Read src/invoices/total.py" in screen_text(app), "the steps")
        assert "sub-agent" not in screen_text(app)


def test_no_workspace_module_reads_meta() -> None:
    source = Path(__file__).parents[1] / "src" / "tranibor_cli"
    for name in ("workspace.py", "run_steps.py", "run_activity.py", "activity.py"):
        code = [
            line
            for line in (source / name).read_text().splitlines()
            if not line.lstrip().startswith(("#", '"'))
        ]
        assert not [line for line in code if '"_meta"' in line or "'_meta'" in line], name


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
            failure={"reason": "budget", "summary": "The run spent its budget of $10.00 at the 31st call."},
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
#: Taller, so that the tree's pane shows the whole expanded run.
TALL = {"80": (80, 80), "160": (160, 76)}


@pytest.mark.parametrize("colour", ["colour", "no-color"])
@pytest.mark.parametrize("columns", ["80", "160"])
def test_the_workspace_snapshot_with_an_expanded_run(
    snap_compare: Any,
    platform: Platform,
    monkeypatch: pytest.MonkeyPatch,
    colour: str,
    columns: str,
) -> None:
    if colour == "no-color":
        monkeypatch.setenv("NO_COLOR", "1")
    _showcase(platform)
    platform.breakdown = {
        "call": 7,
        "at": _iso(NOW),
        "model": "anthropic/sonnet-4.5",
        "prompt_tokens": 40_000,
        "max_input_tokens": 200_000,
        "sections": {"system": 4_000, "messages": 36_000},
    }
    platform.agent_nodes = [
        _agent_node(RUN, None, "agent", tool_calls=3),
        _agent_node("toolu_a", RUN, "subagent", tool_calls=2),
        _agent_node("toolu_b", "toolu_a", "subagent", tool="Task", tool_calls=1, status="completed"),
    ]
    app = make_app(conversation=CID)

    async def settled(pilot: Any) -> None:
        await until(pilot, lambda: app.live and app.conversation is not None, "the workspace")
        await until(pilot, lambda: bool(app.pending), "the decisions")
        await put_cursor_on(pilot, "implement")
        await pilot.press("enter")
        await until(pilot, lambda: "Context: call 7" in screen_text(app), "the run's steps")
        await pilot.pause(0.3)

    assert snap_compare(app, terminal_size=TALL[columns], run_before=settled)


@pytest.mark.parametrize("colour", ["colour", "no-color"])
@pytest.mark.parametrize("columns", ["80", "160"])
@pytest.mark.parametrize("view", ["conversation", "activity", "projects", "decisions", "artefacts"])
def test_the_workspace_snapshot(
    snap_compare: Any,
    platform: Platform,
    monkeypatch: pytest.MonkeyPatch,
    colour: str,
    columns: str,
    view: str,
) -> None:
    if colour == "no-color":
        monkeypatch.setenv("NO_COLOR", "1")
    _showcase(platform)
    app = make_app(conversation=CID)

    async def settled(pilot: Any) -> None:
        await until(pilot, lambda: app.live and app.conversation is not None, "the workspace")
        await until(pilot, lambda: bool(app.pending), "the decisions")
        # The header's counts and amount come from their own reads: a loaded CI runner draws them late.
        await until(pilot, lambda: bool(app.board.in_flight), "what runs")
        await until(pilot, lambda: app.today.usd is not None or app.today.eur is not None, "today's spend")
        await show(pilot, view)
        await pilot.pause(0.3)

    assert snap_compare(app, terminal_size=SIZES[columns], run_before=settled)


@pytest.mark.parametrize("colour", ["colour", "no-color"])
@pytest.mark.parametrize("columns", ["80", "160"])
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_the_projects_tab_snapshot_at_each_depth(
    snap_compare: Any,
    platform: Platform,
    monkeypatch: pytest.MonkeyPatch,
    colour: str,
    columns: str,
    depth: int,
) -> None:
    if colour == "no-color":
        monkeypatch.setenv("NO_COLOR", "1")
    _showcase(platform)
    platform.currency("EUR")
    app = make_app(conversation=CID)
    shown = {1: "FIN-9", 2: "backend", 3: "Read the totals code"}

    async def settled(pilot: Any) -> None:
        await until(pilot, lambda: app.live and app.projects is not None, "the workspace")
        await until(pilot, lambda: bool(app.pending), "the decisions")
        await show(pilot, "projects")
        for level in range(1, depth + 1):
            await pilot.press("enter")
            await until(pilot, lambda key=shown[level]: key in screen_text(app), f"depth {level}")
        await pilot.pause(0.3)

    assert snap_compare(app, terminal_size=SIZES[columns], run_before=settled)


# ─────────────────────────────── the palette (S67-24) ───────────────────────────────


async def _palette(pilot: Any, query: str = "") -> None:
    """Ctrl-K, then the query typed: leaves the palette open on what it narrowed to."""
    await pilot.press("ctrl+k")
    await until(pilot, lambda: "Enter chooses · Up and Down move" in screen_text(pilot.app), "the palette")
    if query:
        await pilot.press(*query)
        await pilot.pause()


def _palette_lines(app: Any) -> list[str]:
    return [
        line
        for line in screen_text(app).splitlines()
        if re.search(r"\b(tab|command|project|item|assistant|organisation)\b", line)
    ]


async def test_ctrl_k_opens_the_palette_from_every_tab_and_escape_closes_it(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        for name in ("conversation", "activity", "projects", "decisions", "artefacts", "live"):
            await show(pilot, name)
            await _palette(pilot)
            await pilot.press("escape")
            await pilot.pause()
            assert "Up and Down move" not in screen_text(app), name
            assert app.current_view == name


async def test_ctrl_k_in_the_composer_does_not_delete_what_is_typed(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await pilot.press(*"hello")
        await pilot.press("home", "ctrl+k")
        await until(pilot, lambda: "Up and Down move" in screen_text(app), "the palette")
        await pilot.press("escape")
        await pilot.pause()
        assert app.query_one("#prompt").value == "hello"


async def test_the_palette_lists_every_kind_with_the_key_that_also_reaches_it(platform: Platform) -> None:
    platform.assistants(
        [
            {"org": "acme", "slug": "finance", "name": "Finance", "status": "ready"},
            {"org": "acme", "slug": "legal", "name": "Legal", "status": "ready"},
        ]
    )
    platform.currency("EUR")
    platform.router.get(f"{V1}/orgs").mock(
        return_value=Response(200, json=[{"slug": "acme", "currency": "EUR"}, {"slug": "globex"}])
    )
    app = make_app()
    async with app.run_test(size=(160, 50)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: app.projects is not None and "globex" in app.organisations, "the lists")
        entries = app.palette_entries()
        kinds = {e.kind for e in entries}
        assert kinds == {"tab", "command", "project", "item", "assistant", "organisation"}
        labels = {e.label for e in entries}
        assert {"Billing API", "Docs site", "FIN-12", "finance Finance", "legal Legal", "globex"} <= labels
        assert "acme" not in labels  # the organisation the person is in is not a place to go to
        assert all(e.reach for e in entries)
        # Nothing is only in the palette: a command's key is bound, a tab's digit is bound.
        bound = {b.key for b in type(app).BINDINGS if isinstance(b, Binding)}
        for entry in entries:
            if entry.kind == "tab":
                assert entry.reach.removeprefix("key ") in bound, entry
            elif entry.kind == "command" and entry.reach != "x":
                assert entry.reach.lower().replace("esc", "escape").replace("-", "+") in bound, entry
            elif entry.kind == "assistant":
                assert "ctrl+a" in bound
        assert "ctrl+k" in bound
        await _palette(pilot)
        shown = screen_text(app)
        assert "F2" in shown and "Ctrl-N" in shown and "key 3" in shown


async def test_a_tab_is_reached_by_name_with_a_few_letters(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await _palette(pilot, "artf")
        await pilot.press("enter")
        await pilot.pause()
        assert app.current_view == "artefacts"
        assert "Up and Down move" not in screen_text(app)


async def test_a_project_is_opened_by_name_then_an_item_by_its_key(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: app.projects is not None, "the projects")
        await _palette(pilot, "billing")
        await pilot.press("enter")
        await until(pilot, lambda: "FIN-9" in screen_text(app), "the project's items")
        assert app.current_view == "projects" and app.depth == 1
        await _palette(pilot, "fin9")
        await pilot.press("enter")
        await until(pilot, lambda: app.depth == 2 and "backend" in screen_text(app), "the item's runs")
        assert app.item is not None and app.item["tracker_key"] == "FIN-9"


async def test_an_item_on_the_board_is_reached_from_another_tab(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: app.projects is not None, "the projects")
        await show(pilot, "decisions")
        await _palette(pilot, "fin12")
        await pilot.press("enter")
        await until(pilot, lambda: app.depth == 2 and app.item_runs is not None, "the item's runs")
        assert app.current_view == "projects" and app.item is not None
        assert app.item["tracker_key"] == "FIN-12"


async def test_an_assistant_is_chosen_by_name(platform: Platform) -> None:
    platform.assistants(
        [
            {"org": "acme", "slug": "finance", "name": "Finance", "status": "ready"},
            {"org": "acme", "slug": "legal", "name": "Legal", "status": "ready"},
        ]
    )
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        assert app.assistant is None
        await _palette(pilot, "legal")
        await pilot.press("enter")
        await pilot.pause()
        assert app.assistant == "legal"
        assert "legal" in screen_text(app)


async def test_another_organisation_is_chosen_by_name_and_read_again(platform: Platform) -> None:
    platform.router.get(f"{V1}/orgs").mock(
        return_value=Response(
            200, json=[{"slug": "acme", "currency": "EUR"}, {"slug": "globex", "currency": "USD"}]
        )
    )
    platform.router.get(f"{V1}/orgs/globex/activity").mock(
        return_value=Response(200, json=platform.contract("ActivityList", {"in_flight": [], "finished": []}))
    )
    platform.router.get(f"{V1}/orgs/globex/activity/stream").mock(return_value=Response(200, text=""))
    platform.router.get(f"{V1}/orgs/globex/projects").mock(
        return_value=Response(200, json={"items": [], "meta": {"has_more": False}})
    )
    platform.router.get(f"{V1}/orgs/globex/costs").mock(return_value=Response(403, json={"detail": "no"}))
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: "globex" in app.organisations, "the organisations")
        assert "acme" in screen_text(app)
        await _palette(pilot, "globex")
        await pilot.press("enter")
        await until(pilot, lambda: app.organisation == "globex" and app.projects == [], "globex read")
        assert "globex" in screen_text(app) and "acme" not in screen_text(app)
        assert app.board.in_flight == {}
        assert app.currency == "USD"


async def test_the_stop_command_asks_for_a_reason_like_the_key(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await put_cursor_on(pilot, "implement")
        await show(pilot, "decisions")
        await _palette(pilot, "stop")
        await pilot.press("enter")
        await pilot.pause()
        assert app.current_view == "activity"
        assert "Stop run run-a1b2c3d4" in screen_text(app)
        await pilot.press(*"wrong branch", "enter")
        await until(pilot, lambda: bool(platform.stops), "the stop")
    assert platform.stops == [(f"/api/v1/runs/{RUN}/cancel", {"reason": "wrong branch"})]


async def test_the_new_conversation_command_starts_one(platform: Platform) -> None:
    app = make_app(conversation=CID)
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: app.conversation is not None, "the conversation")
        await show(pilot, "activity")
        await _palette(pilot, "new conv")
        await pilot.press("enter")
        await pilot.pause()
        assert app.conversation is None and app.conversation_id is None
        assert app.current_view == "conversation"


async def test_the_decide_command_says_when_nothing_waits(platform: Platform) -> None:
    platform.pending([])
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await _palette(pilot, "decide")
        await pilot.press("enter")
        await pilot.pause()
        assert "nothing waits for you" in [n.message for n in app._notifications]


async def test_a_query_nothing_is_called_like_says_so_and_enter_does_nothing(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await _palette(pilot, "zzzq")
        assert "nothing is called like that" in screen_text(app)
        await pilot.press("enter")
        await pilot.pause()
        assert "Up and Down move" in screen_text(app)  # still open
        assert app.current_view == "conversation"


async def test_up_and_down_move_the_choice_and_the_best_match_is_first(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await _palette(pilot, "act")
        await pilot.press("enter")
        await pilot.pause()
        assert app.current_view == "activity"  # "Activity" before "Cancel the turn", "Stop the run…"
        await _palette(pilot, "act")
        await pilot.press("down", "enter")
        await pilot.pause()
        assert app.current_view != "activity" or "Up and Down move" not in screen_text(app)


async def test_the_palette_key_does_nothing_over_a_dialog(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        await put_cursor_on(pilot, "implement")
        await pilot.press("x")
        await pilot.pause()
        assert "Stop run" in screen_text(app)
        await pilot.press("ctrl+k")
        await pilot.pause()
        assert "Up and Down move" not in screen_text(app) and "Stop run" in screen_text(app)


async def test_the_footer_keeps_every_key_and_shows_the_palette(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
        footer = screen_text(app).splitlines()[-1]
        assert "Palette" in footer and "Decide" in footer and "New" in footer and "Quit" in footer


@pytest.mark.parametrize("colour", ["colour", "no-color"])
@pytest.mark.parametrize("columns", ["80", "160"])
@pytest.mark.parametrize("query", ["", "fin"], ids=["empty", "typed"])
def test_the_palette_snapshot(
    snap_compare: Any,
    platform: Platform,
    monkeypatch: pytest.MonkeyPatch,
    colour: str,
    columns: str,
    query: str,
) -> None:
    if colour == "no-color":
        monkeypatch.setenv("NO_COLOR", "1")
    _showcase(platform)
    platform.assistants(
        [
            {"org": "acme", "slug": "finance", "name": "Finance", "status": "ready"},
            {"org": "acme", "slug": "legal", "name": "Legal", "status": "ready"},
        ]
    )
    platform.router.get(f"{V1}/orgs").mock(
        return_value=Response(
            200, json=[{"slug": "acme", "currency": "EUR"}, {"slug": "globex", "currency": "USD"}]
        )
    )
    app = make_app(conversation=CID)

    async def settled(pilot: Any) -> None:
        await until(pilot, lambda: app.live and app.projects is not None, "the workspace")
        await until(pilot, lambda: bool(app.pending) and "globex" in app.organisations, "the lists")
        await _palette(pilot, query)
        await pilot.pause(0.3)

    assert snap_compare(app, terminal_size=SIZES[columns], run_before=settled)


# ─────────────────────────── the entry points (ADR 0154 §3, §8) ───────────────────────────


def test_a_bare_choregos_piped_prints_the_help() -> None:
    from tranibor_cli.main import app

    result = CliRunner().invoke(app, [])  # the runner's stdin and stdout are not terminals
    assert result.exit_code == 0, result.output
    assert (
        "Usage:" in result.output
        and "COMMAND" in result.output
        and "activity" in result.output
        and "chat" in result.output
    )


def test_a_bare_choregos_in_a_terminal_opens_the_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    from tranibor_cli import chat
    from tranibor_cli.main import app

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
    from tranibor_cli import chat

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
    from tranibor_cli.main import app

    result = CliRunner().invoke(app, ["chat"])
    assert result.exit_code == 1 and "needs a terminal" in result.output


def test_version_and_the_other_commands_import_no_textual_module() -> None:
    code = (
        "import sys\n"
        "from tranibor_cli.main import app\n"
        "from typer.testing import CliRunner\n"
        "for words in (['--version'], ['activity', '--help'], ['ask', '--help'], ['live', '--help']):\n"
        "    assert CliRunner().invoke(app, words).exit_code == 0\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'textual'))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]", done.stdout


def test_the_workspace_module_is_the_only_one_importing_textual() -> None:
    source = Path(__file__).parents[1] / "src" / "tranibor_cli"
    importing = sorted(
        path.name
        for path in source.glob("*.py")
        if any(line.startswith(("import textual", "from textual")) for line in path.read_text().splitlines())
    )
    assert importing == ["live_app.py", "workspace.py"]


async def test_a_read_that_lands_after_the_workspace_closed_draws_nothing(platform: Platform) -> None:
    """A journal or activity read can finish while the app tears down: drawing then is a no-op, not a crash
    (seen on CI as `NoMatches: FlightTree` at the end of a test)."""
    app = make_app()
    async with app.run_test(size=(160, 40)) as pilot:
        await ready(pilot)
    app.draw_tree()  # the widgets are gone


async def _open_projects(pilot: Any) -> Any:
    app = pilot.app
    await until(pilot, lambda: app.projects is not None, "the projects")
    await show(pilot, "projects")
    await until(pilot, lambda: "Docs site" in screen_text(app), "the projects listed")
    return app


async def test_the_projects_tab_lists_every_project_even_one_with_no_activity(platform: Platform) -> None:
    platform.currency("EUR")  # a project's month is in euros: an organisation in euros shows it as given
    platform.pending([_pending_action()])
    async with make_app().run_test(size=(160, 40)) as pilot:
        app = await _open_projects(pilot)
        text = screen_text(app)
        assert re.search(r"Billing API +1 +1 +€41\.50", text)
        assert re.search(r"Docs site +0 +0 +€0\.00", text)
        assert "Projects" in text
        assert "Billing API" in pane(app) and "budget" in pane(app)
        await pilot.press("down")
        assert "Docs site" in pane(app)


async def test_enter_goes_down_to_items_then_runs_then_steps_and_backspace_goes_back_up(
    platform: Platform,
) -> None:
    platform.currency("EUR")  # a project's month is in euros: an organisation in euros shows it as given
    async with make_app().run_test(size=(160, 40)) as pilot:
        app = await _open_projects(pilot)
        await pilot.press("enter")
        await until(pilot, lambda: "FIN-12" in screen_text(app), "the items")
        text = screen_text(app)
        assert "Projects › Billing API" in text and "Fix the totals rounding" in text
        assert "€12.50" in text and "FIN-9" in text
        await pilot.press("enter")
        await until(pilot, lambda: "implement" in screen_text(app), "the runs")
        assert "Projects › Billing API › FIN-12" in screen_text(app)
        assert "● running" in screen_text(app) and "#2" in screen_text(app)
        await pilot.press("enter")
        await until(pilot, lambda: "Read the totals code" in screen_text(app), "the steps")
        assert "Projects › Billing API › FIN-12 › implement #2" in screen_text(app)
        await pilot.press("backspace")
        assert "Projects › Billing API › FIN-12" in screen_text(app) and "#2" in screen_text(app)
        assert "Read the totals code" not in screen_text(app)
        await pilot.press("escape")
        assert "Fix the totals rounding" in screen_text(app) and app.depth == 1
        await pilot.press("backspace")
        assert app.depth == 0 and "Docs site" in screen_text(app)


async def test_escape_at_the_top_of_projects_still_cancels_nothing_and_stays(platform: Platform) -> None:
    platform.currency("EUR")  # a project's month is in euros: an organisation in euros shows it as given
    async with make_app().run_test(size=(160, 40)) as pilot:
        app = await _open_projects(pilot)
        await pilot.press("escape", "backspace")
        assert app.depth == 0 and app.current_view == "projects"


async def test_a_project_with_no_work_item_says_so(platform: Platform) -> None:
    platform.currency("EUR")  # a project's month is in euros: an organisation in euros shows it as given
    async with make_app().run_test(size=(160, 40)) as pilot:
        app = await _open_projects(pilot)
        await pilot.press("down", "enter")
        await until(pilot, lambda: "no work item yet" in screen_text(app), "the empty project")


async def test_an_unreadable_projects_route_is_said_not_shown_empty(platform: Platform) -> None:
    platform.currency("EUR")  # a project's month is in euros: an organisation in euros shows it as given
    platform.projects_status = 403
    async with make_app().run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        await show(pilot, "projects")
        await until(pilot, lambda: "not readable" in screen_text(pilot.app), "the refusal")


async def test_the_amounts_follow_the_organisations_currency(platform: Platform) -> None:
    platform.currency("EUR")
    async with make_app(conversation=CID).run_test(size=(160, 40)) as pilot:
        app = pilot.app
        await until(pilot, lambda: app.live and app.conversation is not None, "the workspace")
        text = screen_text(app)
        assert "€1.11 today" in text and "$" not in text.split("today")[0].splitlines()[-1]


async def test_an_unreadable_currency_keeps_each_amounts_own_symbol(platform: Platform) -> None:
    platform.currency(None)
    async with make_app(conversation=CID).run_test(size=(160, 40)) as pilot:
        app = pilot.app
        await until(pilot, lambda: app.live and app.conversation is not None, "the workspace")
        assert "today" in screen_text(app) and "€1.11 today" in screen_text(app)
