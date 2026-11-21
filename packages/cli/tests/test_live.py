# SPDX-License-Identifier: Apache-2.0
"""`choregos live` (S67-28, ADR 0187 §4): the workflow's flow in the terminal.

The API is faked with respx, route by route, every payload validated against `openapi.yaml` (the `contract`
fixture). The screen is driven by Textual's pilot; clocks are pinned (`live_app.now`, `live_app.mono`), so
what a token does at a given instant is the same on every run. The reduction itself (`live_flow`) is tested on
the shared fixtures in `test_live_flow.py`; here the same fixtures go through the drawing.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from tranibor_cli import live_app, live_flow, live_view
from typer.testing import CliRunner

API = "http://api.test"
V1 = f"{API}/api/v1"
NOW = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)
NOW_ISO = "2026-11-18T15:00:00Z"
DAY = {"day_start": "2026-11-18T00:00:00Z"}
FIXTURES = sorted((Path(__file__).resolve().parents[3] / "packages/contracts/fixtures/live").glob("*.json"))


def _node(ident: str, display: str, lane: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": ident,
        "display": display,
        "kind": lane,
        "lane": lane,
        "terminal": lane == "terminal",
        **extra,
    }


def _edge(source: str, target: str, kind: str = "nominal", label: str = "", actor: str | None = None) -> Any:
    return {
        "id": f"{source}->{target}:{kind}",
        "from": source,
        "to": target,
        "kind": kind,
        "label": label,
        "wildcard": False,
        "actor": actor,
    }


#: A delivery workflow with every shape the drawing has: a path, a rejection back up it, an escalation to a
#: person and the answer back, a decline, a release train.
GRAPH: dict[str, Any] = {
    "nodes": [
        _node("triage", "Triage", "agent"),
        _node("plan", "Plan", "agent"),
        _node("implement", "Implement", "agent"),
        _node("review", "Review", "human"),
        _node("merge", "Merge", "train"),
        _node("done", "Done", "terminal", outcome="success"),
        _node("needs_input", "Needs input", "human"),
        _node("declined", "Declined", "terminal", outcome="declined"),
    ],
    "edges": [
        _edge("triage", "plan", actor="triager"),
        _edge("plan", "implement", actor="planner"),
        _edge("implement", "review", actor="coder"),
        _edge("review", "merge", actor="maintainer"),
        _edge("merge", "done"),
        _edge("review", "implement", "reject", "if rejected"),
        _edge("implement", "needs_input", "escalate", "when retries run out"),
        _edge("needs_input", "implement", "nominal", "answered", actor="maintainer"),
        _edge("triage", "declined", "nominal", "decline", actor="triager"),
    ],
}
YAML = "name: delivery\nstates: {}\n"


def _item(ident: str, key: str, state: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": ident,
        "project_slug": "acme-web",
        "tracker_key": key,
        "title": f"Title of {key}",
        "state": state,
        "state_display": state.replace("_", " ").title(),
        "workflow_name": "delivery",
        "totals": {},
        **extra,
    }


def _run(status: str = "running", **extra: Any) -> dict[str, Any]:
    return {
        "id": "run-1",
        "status": status,
        "stage_role": "implement",
        "started_at": "2026-11-18T14:57:00Z",
        "cost_usd": 0.4,
        **extra,
    }


def board() -> list[dict[str, Any]]:
    return [
        _item("w1", "ACME-101", "triage"),
        _item("w2", "ACME-102", "plan"),
        _item("w3", "ACME-103", "implement", current_run=_run()),
        _item(
            "w4",
            "ACME-104",
            "review",
            pending_request={
                "id": "req-1",
                "kind": "approval",
                "requested_at": "2026-11-18T14:00:00Z",
                "payload": {"title": "Merge approval"},
            },
        ),
        _item(
            "w5",
            "ACME-105",
            "needs_input",
            pending_request={
                "id": "req-2",
                "kind": "question",
                "requested_at": "2026-11-18T14:30:00Z",
                "payload": {"question": "Which database?"},
            },
        ),
        _item("w6", "ACME-106", "declined", closed_at="2026-11-18T10:00:00Z"),
        _item("w7", "ACME-107", "done", closed_at="2026-11-18T11:00:00Z"),
        _item("w8", "ACME-108", "plan", workflow_name="other"),  # another workflow's: not on this flow
    ]


def entry(entry_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "kind": "run",
        "id": entry_id,
        "status": "running",
        "project_slug": "acme-web",
        "work_item_id": "w3",
        "work_item_key": "ACME-103",
        "workflow": "delivery",
        "stage_role": "implement",
        "agent_slug": "coder",
        "initiated_by": "ada@example.com",
        "started_at": "2026-11-18T14:57:00Z",
        "ended_at": None,
        "steps": 12,
        "can_stop": True,
    } | changes


class Platform:
    """The routes `choregos live` reads, faked; the activity stream is a queue the test pushes into."""

    def __init__(self, router: respx.Router, contract: Any) -> None:
        self.router, self.contract = router, contract
        self.items = board()
        self.spend: float | None = 2.5
        self.currency = "EUR"
        self.orgs_status = 200
        self.spend_usd_only = False
        self.stream: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.stream_opens = 0
        self.item_reads = 0
        self.stream_params: dict[str, str] = {}
        self.item_params: dict[str, str] = {}
        router.get(f"{V1}/orgs").mock(side_effect=self._orgs)
        router.get(f"{V1}/orgs/acme/projects").mock(
            return_value=Response(
                200,
                # Not run through `contract`: a Project's `$ref`s to ./schemas/ do not resolve in its
                # registry (an empty page would pass and prove nothing).
                json={
                    "items": [
                        {
                            "id": "p-1",
                            "slug": "acme-web",
                            "org": "acme",
                            "name": "Acme Web",
                            "status": "active",
                        }
                    ],
                    "meta": {"has_more": False},
                },
            )
        )
        router.get(f"{V1}/projects/acme-web/workflows").mock(
            return_value=Response(
                200,
                json=[
                    contract(
                        "WorkflowSummary",
                        {"name": "delivery", "version": 3, "is_default": True, "open_items": 7},
                    )
                ],
            )
        )
        router.get(f"{V1}/projects/acme-web/workflows/delivery").mock(
            return_value=Response(
                200,
                json={
                    "name": "delivery",
                    "version": 3,
                    "source": "project",
                    "yaml": YAML,
                    "json": {"initial": "triage"},
                    "checksum": "x",
                    "is_active": True,
                    "is_default": True,
                },
            )
        )
        router.post(f"{V1}/workflows/validate").mock(
            return_value=Response(
                200,
                json=contract(
                    "WorkflowValidation", {"valid": True, "errors": [], "warnings": [], "graph": GRAPH}
                ),
            )
        )
        router.get(f"{V1}/projects/acme-web/work-items").mock(side_effect=self._items)
        router.get(f"{V1}/projects/acme-web/costs").mock(side_effect=self._costs)
        router.get(f"{V1}/orgs/acme/activity/stream").mock(side_effect=self._stream)
        router.get(url__regex=rf"{V1}/work-items/[^/]+/runs").mock(side_effect=self._runs)
        router.get(url__regex=rf"{V1}/runs/[^/]+/events").mock(return_value=Response(200, json=[]))
        router.get(url__regex=rf"{V1}/runs/[^/]+/context-breakdown").mock(
            return_value=Response(404, json={"title": "Not found", "detail": "no breakdown"})
        )

    def _orgs(self, request: Any) -> Response:
        if self.orgs_status != 200:
            return Response(self.orgs_status, json={"title": "Forbidden", "detail": "no"})
        return Response(
            200, json=[self.contract("Org", {"slug": "acme", "name": "Acme", "currency": self.currency})]
        )

    def _items(self, request: Any) -> Response:
        self.item_reads += 1
        self.item_params = dict(request.url.params)
        page = {"items": self.items, "meta": {"has_more": False}}
        return Response(200, json=self.contract("WorkItemPage", page))

    def _costs(self, request: Any) -> Response:
        self.cost_params = dict(request.url.params)
        if self.spend is None:
            return Response(403, json={"title": "Forbidden", "detail": "no"})
        total: dict[str, float] = {"cost_usd": self.spend}
        if not self.spend_usd_only:
            total["cost_eur"] = round(self.spend * 0.9, 2)
        return Response(
            200, json=self.contract("CostReport", {"group_by": "day", "rows": [], "total": total})
        )

    def _runs(self, request: Any) -> Response:
        run = {
            "id": "run-1",
            "status": "running",
            "stage_role": "implement",
            "started_at": "2026-11-18T14:57:00Z",
        }
        return Response(200, json=[run])

    def _stream(self, request: Any) -> Response:
        self.stream_opens += 1
        self.stream_params = dict(request.url.params)

        async def body() -> AsyncIterator[bytes]:
            while True:
                chunk = await self.stream.get()
                if chunk is None:
                    return
                yield chunk

        return Response(200, stream=body(), headers={"content-type": "text/event-stream"})  # type: ignore[arg-type]

    def push(self, event: str, payload: dict[str, Any]) -> None:
        self.contract("ActivityEntry", payload)
        self.stream.put_nowait(f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode())

    def drop(self) -> None:
        self.stream.put_nowait(None)

    def move(self, ident: str, state: str, **extra: Any) -> None:
        """What the next read of the items says: one item in another state."""
        self.items = [
            {**i, "state": state, "state_display": state.replace("_", " ").title(), **extra}
            if i["id"] == ident
            else i
            for i in self.items
        ]


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, float]:
    from tranibor_cli import client as client_module
    from tranibor_cli import theme

    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(theme, "MONOCHROME", False)
    monkeypatch.setenv("TRANIBOR_API_URL", API)
    monkeypatch.setenv("TRANIBOR_TOKEN", "chg_test")
    monkeypatch.setenv("TRANIBOR_ORG", "acme")
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    for name in ("NO_COLOR", "FORCE_COLOR", "TRANIBOR_NO_MOTION"):
        monkeypatch.delenv(name, raising=False)
    clock = {"mono": 1000.0}
    monkeypatch.setattr(live_app, "now", lambda: NOW)
    monkeypatch.setattr(live_app, "mono", lambda: clock["mono"])
    monkeypatch.setattr(live_app, "RELOAD_DELAY_S", 0.01)
    monkeypatch.setattr(live_app, "RECONNECT_S", 0.05)
    return clock


@pytest.fixture
def platform(contract: Any) -> Any:
    with respx.mock(assert_all_called=False) as router:
        yield Platform(router, contract)


def make_app(**kwargs: Any) -> live_app.LiveApp:
    from tranibor_cli.client import AsyncClient, Profile

    project = {"id": "p-1", "slug": "acme-web", "name": "Acme Web"}
    return live_app.LiveApp(
        AsyncClient(Profile(api_url=API, token="chg_test", org="acme")),
        "acme",
        project,
        "delivery",
        is_default=True,
        **kwargs,
    )


def screen_text(app: Any) -> str:
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


def flow_text(app: live_app.LiveApp) -> str:
    assert app.flow.drawing is not None
    return "\n".join(line.plain for line in app.flow.drawing.lines)


async def until(pilot: Any, condition: Callable[[], bool], what: str) -> None:
    for _ in range(100):
        if condition():
            await pilot.pause()
            return
        await pilot.pause(0.05)
    raise AssertionError(f"never happened: {what}\n{screen_text(pilot.app)}")


async def ready(pilot: Any) -> None:
    app = pilot.app
    await until(
        pilot, lambda: (app.flow.loaded and app.flow.connection == "live") or app.flow.loaded, "loaded"
    )
    await until(pilot, lambda: app.flow.drawing is not None, "drawn")


def card(app: live_app.LiveApp, key: str) -> dict[str, Any]:
    assert app.flow.view is not None
    return next(c for c in app.flow.view["cards"] if c["key"] == key)


# ─────────────────────────────────── the board ───────────────────────────────────


async def test_the_board_is_drawn_from_the_workflow_and_its_items(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        text = screen_text(app)
        for station in ("Triage", "Plan", "Implement", "Review", "Merge", "Done", "Needs input", "Declined"):
            assert station in text
        assert "Acme Web" in text and "delivery" in text
        # Another workflow's item is not on this flow; the legacy-free rest is.
        assert "ACME-108" not in text and "ACME-103" in text
        assert platform.stream_params == {"project": "acme-web"}
        assert [c["key"] for c in app.flow.view["cards"]].count("ACME-101") == 1  # type: ignore[index]


async def test_the_summary_counts_what_the_flow_holds_and_spend_is_in_the_orgs_currency(
    platform: Platform,
) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        text = screen_text(app)
        assert "1 in flight" in text
        assert "2 waiting for a person" in text
        assert "0 failed" in text
        assert "2 done today" in text
        assert "€2.25 spent today" in text  # the euro total, not the dollar one relabelled
        assert platform.cost_params["workflow"] == "delivery"


async def test_a_dollar_organisation_is_shown_dollars_and_an_unreadable_spend_is_a_dash_not_zero(
    platform: Platform,
) -> None:
    platform.currency = "USD"
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        assert "$2.50 spent today" in screen_text(app)
    platform.spend = None
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        assert "— spent today" in screen_text(app)


async def test_every_status_is_a_glyph_and_a_word(platform: Platform) -> None:
    platform.items.append(_item("w9", "ACME-109", "plan", failure={"message": "the gateway refused it"}))
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        text = flow_text(app)
        assert "ACME-103" in text and "3:00" in text  # running: elapsed
        assert "⏸ ACME-104  approval" in text.replace("  ", "  ")
        assert "question" in text  # waiting: what for
        assert "✗ ACME-109" in text and "stalled" in text  # failed: why
        assert "○ ACME-101" in text  # idle
        assert "✓ ACME-107" in text  # done
        assert "■ ACME-106" in text  # declined


# ─────────────────────────────────── what moves ───────────────────────────────────


async def test_a_run_starting_turns_a_token_blue_and_ticks_the_ticker(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        assert card(app, "ACME-101")["status"] == "idle"
        platform.push(
            "activity.upsert",
            entry(
                "run-9",
                work_item_id="w1",
                work_item_key="ACME-101",
                stage_role="triage",
                started_at="2026-11-18T14:59:50Z",
            ),
        )
        await until(pilot, lambda: card(app, "ACME-101")["status"] == "running", "the run")
        assert "started triage" in screen_text(app)
        assert app.flow.connection == "live"


async def test_a_token_that_changes_station_moves_to_its_new_station_and_travels_its_arrow(
    platform: Platform, environment: dict[str, float]
) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        platform.move("w3", "review", current_run=None)
        platform.push("activity.ended", entry("run-1", status="succeeded", ended_at="2026-11-18T15:00:00Z"))
        await until(pilot, lambda: card(app, "ACME-103")["lane"] == "review", "the move")
        assert app.flow.view is not None
        assert any("moved" in str(t) or t["kind"] == "moved" for t in app.flow.view["ticker"])
        assert "w3" in app.flow.drawing.token_at and app.flow.moves["w3"][0] is not None  # type: ignore[union-attr]
        # Mid-travel: the dot is on an arrow, the arrival is not drawn yet.
        environment["mono"] += live_app.TRAVEL_S / 2
        app.flow.redraw(force=True)
        text = flow_text(app)
        assert "●" in text.split("\n")[3] or "●" in "\n".join(text.split("\n")[:3])
        assert "ACME-103" in text
        # Arrived: no dot on an arrow, the token is at its station, highlighted for a while.
        environment["mono"] += live_app.TRAVEL_S
        app.flow.redraw(force=True)
        assert app.flow.motion_now().fresh["w3"] > 0.9
        environment["mono"] += live_app.HIGHLIGHT_S + 1
        app.flow.redraw(force=True)
        assert "w3" not in app.flow.moves


async def test_the_arrow_a_token_took_is_lit_briefly(
    platform: Platform, environment: dict[str, float]
) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        platform.move("w3", "review", current_run=None)
        platform.push("activity.ended", entry("run-1", status="succeeded", ended_at="2026-11-18T15:00:00Z"))
        await until(pilot, lambda: card(app, "ACME-103")["lane"] == "review", "the move")
        edge = app.flow.moves["w3"][0]
        assert edge in app.flow.motion_now().lit
        environment["mono"] += live_app.LIT_S + 0.1
        assert edge not in app.flow.motion_now().lit


async def test_a_failed_run_says_why_on_the_token_and_in_the_ticker(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        platform.move("w3", "implement", current_run=None)  # the item no longer has a live run
        platform.push(
            "activity.ended",
            entry(
                "run-1",
                status="failed",
                ended_at="2026-11-18T15:00:00Z",
                failure={"reason": "loop", "summary": "the same edit twelve times"},
            ),
        )
        await until(pilot, lambda: card(app, "ACME-103")["status"] == "failed", "the failure")
        assert "✗ ACME-103" in flow_text(app) and "loop" in flow_text(app)
        assert "failed implement (loop)" in screen_text(app)
        assert "1 failed" in screen_text(app)


async def test_a_human_wait_appears_when_the_items_are_read_again(
    platform: Platform, environment: dict[str, float]
) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        platform.move(
            "w3",
            "review",
            current_run=None,
            pending_request={
                "id": "req-3",
                "kind": "approval",
                "requested_at": "2026-11-18T15:00:00Z",
                "payload": {"title": "Ship it?"},
            },
        )
        platform.push("activity.ended", entry("run-1", status="succeeded", ended_at="2026-11-18T15:00:00Z"))
        await until(pilot, lambda: card(app, "ACME-103")["status"] == "waiting", "the wait")
        environment["mono"] += live_app.TRAVEL_S + 0.1  # it has arrived: its glyph is back
        app.flow.redraw(force=True)
        assert "⏸ ACME-103" in flow_text(app)
        assert "3 waiting for a person" in screen_text(app)


async def test_a_run_ending_re_reads_the_items_once_for_several_endings(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        before = platform.item_reads
        for n in range(3):
            platform.push(
                "activity.ended", entry(f"run-x{n}", status="succeeded", ended_at="2026-11-18T15:00:00Z")
            )
        await until(pilot, lambda: platform.item_reads > before, "a read")
        await pilot.pause(0.2)
        assert platform.item_reads - before <= 2


async def test_a_quiet_stream_is_live_before_its_first_event(platform: Platform) -> None:
    """A flow where nothing happens only gets the server's pings: once the stream is open it is LIVE, not
    RECONNECTING (seen on a real instance, S67-13)."""
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: platform.stream_opens > 0, "the stream")
        await until(pilot, lambda: app.flow.connection == "live", "live without an event")
        assert "LIVE" in screen_text(app) and "RECONNECTING" not in screen_text(app)


async def test_a_dropped_stream_is_said_then_followed_again(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tranibor_cli import live_app

    # A reopened stream is live at once: wait long enough between attempts for the drop to be seen.
    monkeypatch.setattr(live_app, "RECONNECT_S", 0.6)
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        await until(pilot, lambda: app.flow.connection == "live" or platform.stream_opens > 0, "the stream")
        platform.push("activity.upsert", entry("run-1"))
        await until(pilot, lambda: app.flow.connection == "live", "live")
        platform.drop()
        await until(pilot, lambda: app.flow.connection == "reconnecting", "the drop")
        assert "RECONNECTING" in screen_text(app)
        await until(pilot, lambda: platform.stream_opens >= 2, "a second connection")
        platform.push("activity.upsert", entry("run-1"))
        await until(pilot, lambda: app.flow.connection == "live", "live again")
        assert "LIVE" in screen_text(app)


async def test_the_screen_writes_nothing(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        for key in ("right", "right", "left", "enter", "escape"):
            await pilot.press(key)
        written = [
            c
            for c in platform.router.calls
            if c.request.method != "GET" and "validate" not in str(c.request.url)
        ]
        assert written == []


# ─────────────────────────────────── keys ───────────────────────────────────


async def test_arrows_walk_the_tokens_and_enter_opens_the_item(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        first = app.flow.selected
        await pilot.press("right")
        assert app.flow.selected != first
        await pilot.press("left")
        assert app.flow.selected == first
        app.flow.selected = "w3"
        app.flow.redraw(force=True)
        await pilot.press("enter")
        await until(pilot, lambda: "run-1" in screen_text(app), "the item's run")
        text = screen_text(app)
        assert "ACME-103" in text and "Title of ACME-103" in text
        assert "running" in text
        await pilot.press("escape")
        await until(pilot, lambda: "Title of ACME-103" not in screen_text(app), "closed")


async def test_a_failed_items_reason_is_in_full_when_it_is_opened(platform: Platform) -> None:
    platform.items.append(
        _item("w9", "ACME-109", "plan", failure={"message": "the gateway refused the call"})
    )
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        app.flow.selected = "w9"
        await pilot.press("enter")
        await until(pilot, lambda: "the gateway refused the call" in screen_text(app), "the reason")


async def test_q_quits(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        await pilot.press("q")
        await pilot.pause(0.1)
    assert app.return_code in (0, None)


async def test_the_selected_token_stays_in_view_on_a_narrow_terminal(platform: Platform) -> None:
    app = make_app()
    async with app.run_test(size=(60, 30)) as pilot:
        await ready(pilot)
        app.flow.selected = "w7"  # in the last station, far to the right
        app.flow.redraw(force=True)
        await pilot.pause(0.2)
        assert app.query_one("#flow-scroll").scroll_x > 0
        assert "ACME-107" in screen_text(app)


# ─────────────────────────────────── motion and colour ───────────────────────────────────


@pytest.mark.parametrize("how", ["NO_COLOR", "TRANIBOR_NO_MOTION", "flag"])
async def test_reduced_motion_has_no_spinner_and_no_travelling_token(
    platform: Platform, environment: dict[str, float], monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    if how != "flag":
        monkeypatch.setenv(how, "1")
    app = make_app(motion=how != "flag")
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        assert app.flow.motion is False
        platform.move("w3", "review", current_run=None)
        platform.push("activity.ended", entry("run-1", status="succeeded", ended_at="2026-11-18T15:00:00Z"))
        await until(pilot, lambda: card(app, "ACME-103")["lane"] == "review", "the move")
        assert app.flow.motion_now().travel == {} and app.flow.motion_now().frame is None
        assert not any(c in flow_text(app) for c in live_view.SPINNER)
        assert "ACME-103" in flow_text(app)  # at its station at once, nothing in between


async def test_motion_is_on_by_default_and_the_spinner_turns(
    platform: Platform, environment: dict[str, float]
) -> None:
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        assert app.flow.motion is True
        first = flow_text(app)
        environment["mono"] += 0.1
        app.flow.redraw(force=True)
        assert flow_text(app) != first  # the spinner's next frame


# ─────────────────────────────────── the drawing ───────────────────────────────────


def drawing(
    width: int, *, ascii_only: bool = False, motion: live_view.Motion | None = None
) -> live_view.Drawing:
    view = live_flow.reduce(GRAPH, board(), [], NOW_ISO, DAY)
    return live_view.draw(view, GRAPH, "triage", width=width, motion=motion, ascii_only=ascii_only)


def plain(d: live_view.Drawing) -> str:
    return "\n".join(line.plain for line in d.lines)


def test_the_path_is_the_longest_nominal_run_from_the_initial_state() -> None:
    assert live_view.nominal_path(GRAPH, "triage") == [
        "triage",
        "plan",
        "implement",
        "review",
        "merge",
        "done",
    ]
    assert live_view.nominal_path({"nodes": [], "edges": []}) == []


def test_side_states_hang_below_the_station_they_are_reached_from() -> None:
    lines = plain(drawing(300)).split("\n")
    rows = [i for i, line in enumerate(lines) if "Needs input" in line or "Declined" in line]
    path_row = next(i for i, line in enumerate(lines) if "Triage" in line)
    assert rows and all(r > path_row for r in rows)
    # Declined falls under Triage (its source), Needs input under Implement.
    declined = (
        lines[rows[0]].index("Declined") if "Declined" in lines[rows[0]] else lines[rows[1]].index("Declined")
    )
    assert declined < lines[path_row].index("Plan")


def test_a_rejection_is_drawn_over_the_path_with_its_label() -> None:
    text = plain(drawing(300))
    top = text.split("\n")[0]
    assert "if rejected" in top and "▼" in text.split("\n")[1]
    assert top.rstrip().endswith("─╮") and "┄" not in top  # one unbroken arc, not a dashed one


def test_the_stations_collapse_to_counts_when_the_terminal_is_too_narrow() -> None:
    full, narrow = drawing(300), drawing(100)
    assert not full.collapsed and narrow.collapsed
    assert narrow.width < full.width
    assert "⏸1" in plain(narrow) and "●1" in plain(narrow)
    assert "ACME-103" not in plain(narrow)  # a count, not the names, until a token is selected


def test_a_collapsed_station_names_the_selected_token() -> None:
    picked = drawing(100, motion=live_view.Motion(selected="w3"))
    assert "ACME-103" in plain(picked)


def test_more_tokens_than_a_station_holds_say_how_many_more_and_keep_the_one_that_matters() -> None:
    items = [_item(f"x{n}", f"ACME-{200 + n}", "plan") for n in range(9)] + [
        _item("hot", "ACME-999", "plan", failure={"message": "boom"})
    ]
    view = live_flow.reduce(GRAPH, items, [], NOW_ISO, DAY)
    text = "\n".join(line.plain for line in live_view.draw(view, GRAPH, "triage", width=300).lines)
    assert "+6 more" in text and "ACME-999" in text  # the failed one is never the one left out


def test_ascii_draws_the_same_flow_with_plain_characters() -> None:
    text = plain(drawing(300, ascii_only=True))
    assert text.isascii()
    for word in ("Triage", "ACME-103", "approval", "if rejected"):
        assert word in text
    assert "* ACME-103" in text and "! ACME-104" in text


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
@pytest.mark.parametrize("width", [100, 180])
def test_every_shared_fixture_goes_through_the_renderer(path: Path, width: int) -> None:
    fixture = json.loads(path.read_text())
    view = live_flow.reduce(
        fixture["graph"], fixture["items"], fixture["steps"], fixture["now"], fixture["options"]
    )
    for ascii_only in (False, True):
        drawn = live_view.draw(view, fixture["graph"], None, width=width, ascii_only=ascii_only)
        text = "\n".join(line.plain for line in drawn.lines)
        for lane in view["lanes"]:
            assert lane["display"][:8] in text, (path.name, lane["display"])
        if not drawn.collapsed:
            keys = [c["key"] for c in view["cards"]]
            assert all(k in text for k in keys[:3])
        assert drawn.order == [cid for lane_ in drawn.boxes.values() for cid in lane_.cards]
        if ascii_only:
            assert text.isascii()


def test_a_workflow_without_a_graph_still_shows_the_lanes_it_has() -> None:
    fixture = json.loads(next(p for p in FIXTURES if p.name.startswith("07")).read_text())
    view = live_flow.reduce(None, fixture["items"], [], fixture["now"], fixture["options"])
    drawn = live_view.draw(view, None, None, width=200)
    assert all(lane["display"] in plain(drawn) for lane in view["lanes"])


def test_a_failure_reason_is_a_word() -> None:
    assert live_view.reason_word("max_turns") == "turns"
    assert live_view.reason_word("loop") == "loop"
    assert live_view.reason_word(None) == "failed"
    assert live_view.reason_long("budget") == "budget spent"
    assert live_view.reason_long("something_new") == "something new"  # an unknown reason is still said


async def test_an_unreadable_currency_shows_the_spend_in_its_own_symbol(platform: Platform) -> None:
    platform.orgs_status = 403
    platform.spend_usd_only = True
    app = make_app()
    async with app.run_test(size=(190, 40)) as pilot:
        await ready(pilot)
        assert "$2.50 spent today" in screen_text(app)  # dollars, said as dollars: never relabelled euros


# ─────────────────────────────────── the entry point ───────────────────────────────────


def test_live_needs_a_terminal() -> None:
    from tranibor_cli.main import app

    done = CliRunner().invoke(app, ["live"])
    assert done.exit_code != 0
    assert "needs a terminal" in done.output


def test_live_is_documented_in_the_help() -> None:
    from tranibor_cli.main import app

    done = CliRunner().invoke(app, ["live", "--help"])
    assert done.exit_code == 0
    # Rich colours each option's dashes apart when GITHUB_ACTIONS forces a terminal (as on CI).
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", done.output)
    for option in ("--project", "--workflow", "--org", "--no-motion"):
        assert option in text


def test_the_project_and_the_workflow_are_picked_or_refused_with_the_choices() -> None:
    from tranibor_cli import live

    projects = [{"slug": "acme-web", "id": "p-1"}, {"slug": "globex-api", "id": "p-2"}]
    assert live._pick(projects, "globex-api")["id"] == "p-2"
    assert live._pick(projects[:1], None)["slug"] == "acme-web"
    with pytest.raises(LookupError, match="acme-web, globex-api"):
        live._pick(projects, None)
    with pytest.raises(LookupError, match="no project 'nope'"):
        live._pick(projects, "nope")
    workflows = [{"name": "delivery", "is_default": True}, {"name": "support", "is_default": False}]
    assert live._workflow(workflows, None) == ("delivery", True)
    assert live._workflow(workflows, "support") == ("support", False)
    with pytest.raises(LookupError, match="delivery, support"):
        live._workflow(workflows, "nope")
    with pytest.raises(LookupError, match="no default workflow"):
        live._workflow(workflows[1:], None)


def test_live_imports_no_textual_until_the_screen_opens() -> None:
    code = (
        "import sys\n"
        "from tranibor_cli.main import app\n"
        "from typer.testing import CliRunner\n"
        "assert CliRunner().invoke(app, ['live', '--help']).exit_code == 0\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] == 'textual'))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]", done.stdout


# ─────────────────────────────────── snapshots (SVG) ───────────────────────────────────

SIZES = {"100": (100, 30), "180": (180, 30)}


def _scripted(platform: Platform, travel: bool = False) -> Callable[[Any], Any]:
    """The same board for every snapshot: after a token moved, a run failed with a reason, a person waited."""
    app_holder: dict[str, live_app.LiveApp] = {}

    async def settled(pilot: Any) -> None:
        app = pilot.app
        app_holder["app"] = app
        await ready(pilot)
        platform.items.append(_item("w9", "ACME-109", "plan"))
        platform.move("w3", "review", current_run=None)
        platform.push("activity.ended", entry("run-1", status="succeeded", ended_at="2026-11-18T14:59:30Z"))
        await until(pilot, lambda: card(app, "ACME-103")["lane"] == "review", "the move")
        platform.push(
            "activity.ended",
            entry(
                "run-5",
                work_item_id="w2",
                work_item_key="ACME-102",
                stage_role="plan",
                status="failed",
                started_at="2026-11-18T14:58:00Z",
                ended_at="2026-11-18T14:59:40Z",
                failure={"reason": "budget", "summary": "spent"},
            ),
        )
        await until(pilot, lambda: card(app, "ACME-102")["status"] == "failed", "the failure")
        await pilot.pause(0.2)
        clock = live_app.mono()
        # Arrival at the Review station (travel=False) or half way along its arrow (travel=True).
        offset = live_app.TRAVEL_S / 2 if travel else live_app.TRAVEL_S + 0.5
        app.flow.moves["w3"] = (app.flow.moves["w3"][0], clock - offset)
        app.flow.selected = "w4"
        app.flow.redraw(force=True)
        await pilot.pause(0.1)

    return settled


@pytest.mark.parametrize("colour", ["colour", "no-color"])
@pytest.mark.parametrize("columns", ["100", "180"])
def test_the_flow_snapshot(
    snap_compare: Any, platform: Platform, monkeypatch: pytest.MonkeyPatch, colour: str, columns: str
) -> None:
    if colour == "no-color":
        monkeypatch.setenv("NO_COLOR", "1")
    assert snap_compare(make_app(), terminal_size=SIZES[columns], run_before=_scripted(platform))


def test_the_flow_snapshot_with_a_token_mid_travel(snap_compare: Any, platform: Platform) -> None:
    assert snap_compare(make_app(), terminal_size=SIZES["180"], run_before=_scripted(platform, travel=True))


def test_the_ascii_snapshot(snap_compare: Any, platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tranibor_cli.theme.unicode_ok", lambda console: False)
    assert snap_compare(make_app(), terminal_size=SIZES["180"], run_before=_scripted(platform))


def test_the_clock_is_pinned_for_the_snapshots() -> None:
    assert os.environ["TZ"] == "UTC"


def test_the_ticker_is_newest_first_by_time_with_a_space_after_the_key() -> None:
    view = live_flow.reduce(GRAPH, board(), [], NOW_ISO, DAY)
    view["ticker"] = [
        {"kind": "succeeded", "at": "2026-11-18T14:59:30Z", "key": "ACME-103", "stage": "implement"},
        {
            "kind": "failed",
            "at": "2026-11-18T15:00:10Z",
            "key": "ACME-102",
            "stage": "plan",
            "reason": "budget",
        },
        {"kind": "started", "at": "2026-11-18T15:00:00Z", "key": "ACME-101", "stage": "triage"},
    ]
    lines = [line.plain for line in live_view.ticker_lines(view, 5, 100, False)]
    assert [line.split()[0] for line in lines] == ["15:00:10", "15:00:00", "14:59:30"]
    assert "ACME-102 failed" in lines[0].replace("  ", " ")
    assert lines[0].index("failed") > lines[0].index("ACME-102") + len("ACME-102")


def test_a_station_subtitle_is_the_agents_name_in_the_boxs_full_width() -> None:
    for width in (300, 100):
        text = plain(drawing(width))
        assert "agent ·" not in text and "human ·" not in text
        assert "…" not in "\n".join(text.split("\n")[3:5])
    assert "coder" in plain(drawing(300)) and "release train" in plain(drawing(300))
