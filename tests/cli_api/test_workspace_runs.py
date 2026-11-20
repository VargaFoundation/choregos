# SPDX-License-Identifier: Apache-2.0
"""The workspace's expanded run nodes against the real API (S67-14): several runs open at once, each read
over HTTP from the journal the API serves, signed in by the device flow."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx

from .conftest import ORG, Api, lay_run, project


def _call(seq: int, call_id: str, title: str, status: str) -> dict[str, Any]:
    update = {
        "sessionUpdate": "tool_call",
        "toolCallId": call_id,
        "title": title,
        "kind": "read",
        "status": status,
    }
    return {
        "seq": seq,
        "type": "session/update",
        "ts": "2026-11-10T09:00:00+00:00",
        "payload": {"update": update},
    }


def _screen(app: Any) -> str:
    return "\n".join(strip.text for strip in app.screen._compositor.render_strips())


async def _until(pilot: Any, condition: Callable[[], bool], what: str) -> None:
    for _ in range(200):
        if condition():
            await pilot.pause()
            return
        await pilot.pause(0.05)
    raise AssertionError(f"never happened: {what}\n{_screen(pilot.app)}")


async def test_three_open_runs_each_show_the_journal_the_api_serves(
    signed_in: Api, console: httpx.Client
) -> None:
    from tranibor_cli.client import AsyncClient, Profile
    from tranibor_cli.workspace import FlightTree, Workspace

    pid = project(console)
    for n in (1, 2, 3):
        lay_run(
            signed_in,
            pid,
            f"run-ws-{n}",
            backend="opencode",
            status="running",
            events=[
                _call(1, f"c{n}a", f"Read alpha{n}.py", "completed"),
                _call(2, f"c{n}b", f"Edit beta{n}.py", "pending"),
            ],
        )
    saved = json.loads((signed_in.home / "session.json").read_text(encoding="utf-8"))
    app = Workspace(AsyncClient(Profile(api_url=signed_in.url, token=saved["token"], org=ORG)), ORG)
    async with app.run_test(size=(180, 90)) as pilot:
        await _until(pilot, lambda: app.live, "the activity")
        app.action_show_view("activity")
        await pilot.pause()
        tree = pilot.app.query_one(FlightTree)
        tree.focus()

        def run_node(n: int) -> Any:
            """The node of run `n`, found again each time: a redraw replaces the nodes."""
            return next(
                node
                for node in app._by_key.values()
                if (node.data or {}).get("kind") == "run" and node.data["entry"]["id"].endswith(f"run-ws-{n}")
            )

        for n in (1, 2, 3):
            tree.move_cursor(run_node(n), animate=False)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        await _until(
            pilot, lambda: all(f"Edit beta{n}.py" in _screen(app) for n in (1, 2, 3)), "the three journals"
        )
        text = _screen(app)
    assert text.count("Steps  2 tool calls · 1 finished") == 3, text
    assert "Context: not measured" in text
    await asyncio.sleep(0)
