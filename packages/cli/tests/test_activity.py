# SPDX-License-Identifier: Apache-2.0
"""`choregos activity` (S67-02, ADR 0154 §2-3, §9): the live tree of what runs, colour never alone.

The API is faked with respx; every entry the fake serves is validated against `openapi.yaml` (`contract`).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
V1 = f"{API}/api/v1"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tranibor_cli import activity, theme
    from tranibor_cli import client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(theme, "MONOCHROME", False)
    monkeypatch.setattr(activity, "REDRAW_EVERY_S", 0.01)
    # Elapsed times are read against a pinned clock: a loaded machine must not move "4m12s".
    monkeypatch.setattr(activity, "clock", lambda: NOW)
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_TOKEN", "chg_test")
    monkeypatch.setenv("CHOREGOS_ORG", "acme")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _run_entry(run_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "kind": "run",
        "id": run_id,
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


def _session(session_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "kind": "session",
        "id": session_id,
        "status": "open",
        "agent_slug": "support",
        "started_at": _iso(NOW - timedelta(seconds=30)),
        "spend": None,
        "can_stop": True,
    } | changes


def _node(node_id: str, parent: str | None, kind: str, **changes: Any) -> dict[str, Any]:
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


class Platform:
    def __init__(self, router: respx.Router, contract: Any) -> None:
        self.router, self.contract = router, contract
        self.listing([_run_entry("run-a1b2c3d4e5f6a7b8")], [])
        self.today(1.2345)
        # Every run is flat unless a test says otherwise: the run's own agent and nothing under it.
        self.served: dict[str, Response] = {}
        self.asked: list[str] = []

        def answer(request: Any) -> Response:
            run_id = request.url.path.split("/")[-2]
            self.asked.append(run_id)
            flat = {"items": [_node(run_id, None, "agent")]}
            return self.served.get(run_id) or Response(200, json=contract("RunAgentList", flat))

        self.router.get(url__regex=rf"{re.escape(V1)}/runs/[^/]+/agents").mock(side_effect=answer)

    def listing(self, in_flight: list[dict[str, Any]], finished: list[dict[str, Any]]) -> None:
        body = self.contract("ActivityList", {"in_flight": in_flight, "finished": finished})
        self.listed = self.router.get(f"{V1}/orgs/acme/activity").mock(return_value=Response(200, json=body))

    def agents(self, run_id: str, *nodes: dict[str, Any], status: int = 200) -> None:
        body: Any = {"title": "Forbidden", "detail": "no"}
        if status == 200:
            body = self.contract("RunAgentList", {"items": list(nodes)})
        self.served[run_id] = Response(status, json=body)

    def today(self, cost: float | None, status: int = 200) -> None:
        body: dict[str, Any] = {"title": "Forbidden", "detail": "no"}
        if status == 200:
            body = {"group_by": "day", "rows": [], "total": {"cost_usd": cost, "cost_eur": 0.0}}
            self.contract("CostReport", body)
        self.costs = self.router.get(f"{V1}/orgs/acme/costs").mock(return_value=Response(status, json=body))

    def stream(self, *events: tuple[str, dict[str, Any]]) -> None:
        for _, entry in events:
            self.contract("ActivityEntry", entry)
        body = "".join(
            f"id: {n}\nevent: {name}\ndata: {json.dumps(data)}\n\n" for n, (name, data) in enumerate(events)
        )
        self.streamed = self.router.get(f"{V1}/orgs/acme/activity/stream").mock(
            return_value=Response(200, text=body, headers={"content-type": "text/event-stream"})
        )


@pytest.fixture
def platform(contract: Any) -> Any:
    with respx.mock(assert_all_called=False) as router:
        yield Platform(router, contract)


def _run(*words: str, env: dict[str, str] | None = None) -> Any:
    from tranibor_cli.main import app

    return CliRunner().invoke(app, ["activity", *words], env=env)


def _said(result: Any) -> str:
    return " ".join(ANSI.sub("", result.output).split())


def test_the_tree_reads_project_work_item_run_with_status_time_tokens_and_cost(platform: Platform) -> None:
    result = _run()
    assert result.exit_code == 0, result.output
    lines = [" ".join(line.strip("│├└─ ").split()) for line in ANSI.sub("", result.output).splitlines()]
    project = next(n for n, line in enumerate(lines) if line.startswith("billing-api"))
    assert lines[project + 1].startswith("FIN-12 delivery")
    run = lines[project + 2]
    assert run.startswith("● running implement · coder · sonnet-4.5")
    assert re.search(r"4m12s · 12\.3k in / 800 out · \$0\.84 run run-a1b2c3d4", run), run
    assert "acme · 1 running · — waiting for you · $1.23 today" in _said(result)


def test_unknown_spend_is_a_dash_never_zero(platform: Platform) -> None:
    platform.listing([_run_entry("run-1", spend=None), _session("ses-1")], [])
    said = _said(_run())
    assert "· — run run-1" in said and "· — session ses-1" in said
    assert "$0.00" not in said and "0 in / 0 out" not in said


def test_sessions_are_grouped_apart_and_each_status_reads_as_a_word(platform: Platform) -> None:
    platform.listing(
        [_run_entry("run-q", status="queued"), _session("ses-1", project_slug="billing-api")],
        [
            _run_entry("run-ok", status="succeeded", ended_at=_iso(NOW)),
            _run_entry("run-ko", status="failed", ended_at=_iso(NOW), work_item_key="FIN-13"),
            _run_entry("run-st", status="cancelled", ended_at=_iso(NOW), work_item_key="FIN-14"),
        ],
    )
    said = _said(_run())
    for words in ("○ queued implement", "● running session · support", "✓ done", "✗ failed", "■ cancelled"):
        assert words in said, words
    assert said.index("your sessions and conversations") < said.index("Ended in the last hour")
    assert said.index("your sessions and conversations") < said.index("● running session · support")
    assert said.index("■ cancelled") > said.index("Ended in the last hour")


def test_the_work_item_line_carries_its_title_when_the_platform_says_it(platform: Platform) -> None:
    platform.listing([_run_entry("run-t", work_item_title="Reconcile the March ledger")], [])
    assert "FIN-12 Reconcile the March ledger" in _said(_run())


def test_nothing_running_says_so(platform: Platform) -> None:
    platform.listing([], [])
    assert "nothing is running" in _said(_run())


def test_the_filters_are_sent_as_query_parameters(platform: Platform) -> None:
    result = _run("--project", "billing-api", "--workflow", "delivery", "--agent", "coder", "--kind", "run")
    assert result.exit_code == 0, result.output
    params = dict(platform.listed.calls.last.request.url.params)
    assert params == {"project": "billing-api", "workflow": "delivery", "agent": "coder", "kind": "run"}
    day = date.today().isoformat()
    assert dict(platform.costs.calls.last.request.url.params) == {"since": day, "until": day}


def test_a_person_who_may_not_read_the_costs_sees_a_dash(platform: Platform) -> None:
    platform.today(None, status=403)
    result = _run()
    assert result.exit_code == 0, result.output
    assert "— waiting for you · — today" in _said(result)


def test_a_refused_listing_is_a_red_line(platform: Platform) -> None:
    platform.router.get(f"{V1}/orgs/acme/activity").mock(
        return_value=Response(403, json={"detail": "not a member"})
    )
    result = _run()
    assert result.exit_code == 1 and "403 — not a member" in _said(result)


def test_watch_moves_an_ended_entry_from_in_flight_to_ended(platform: Platform) -> None:
    started = _run_entry("run-new", work_item_key="FIN-20", stage_role="review")
    platform.stream(
        ("activity.upsert", started),
        ("activity.ended", _run_entry("run-a1b2c3d4e5f6a7b8", status="succeeded", ended_at=_iso(NOW))),
    )
    result = _run("--watch", "--project", "billing-api")
    assert result.exit_code == 0, result.output
    final = ANSI.sub("", result.output).split("In flight")[-1]
    in_flight, ended = final.split("Ended in the last hour")
    assert "FIN-20" in in_flight and "● running review" in " ".join(in_flight.split())
    assert "run-a1b2c3d4" not in in_flight
    assert "✓ done" in ended and "run-a1b2c3d4" in ended
    assert dict(platform.streamed.calls.last.request.url.params) == {"project": "billing-api"}


def test_board_keeps_the_newest_first_and_an_upsert_that_ended_moves_it() -> None:
    from tranibor_cli.activity import Board

    board = Board.of({"in_flight": [_run_entry("a")], "finished": []})
    board.apply("activity.upsert", _run_entry("b"))
    assert list(board.in_flight) == ["b", "a"]
    board.apply("activity.upsert", _run_entry("a", steps=50))
    assert list(board.in_flight) == ["b", "a"] and board.in_flight["a"]["steps"] == 50
    board.apply("activity.upsert", _run_entry("b", status="failed"))
    assert list(board.in_flight) == ["a"] and list(board.finished) == ["b"]
    board.apply("activity.upsert", {"status": "running"})
    assert list(board.in_flight) == ["a"]
    assert board.running() == 1


def test_elapsed_and_counts_read_as_a_person_reads_them() -> None:
    from tranibor_cli.activity import elapsed, spend

    start = NOW - timedelta(hours=1, minutes=3, seconds=5)
    assert elapsed({"started_at": _iso(start)}, NOW) == "1h03m"
    assert elapsed({"started_at": _iso(start), "ended_at": _iso(start + timedelta(seconds=9))}, NOW) == "9s"
    assert elapsed({}, NOW) == "—"
    assert spend({"tokens_in": 2_500_000, "tokens_out": 999, "cost_usd": 3}) == "2.5M in / 999 out · $3.00"
    assert spend(None) == "—"


def test_no_color_gives_no_escape_sequence_and_keeps_the_words(platform: Platform) -> None:
    forced = {"FORCE_COLOR": "1", "TTY_COMPATIBLE": "1"}
    assert ANSI.search(_run(env=forced).output), (
        "the forced colour run must colour, or the test proves nothing"
    )
    plain = _run(env=forced | {"NO_COLOR": "1"})
    assert plain.exit_code == 0 and not ANSI.search(plain.output)
    assert "● running implement" in _said(plain)


def test_an_ascii_terminal_gets_ascii_glyphs(platform: Platform, monkeypatch: pytest.MonkeyPatch) -> None:
    from tranibor_cli import theme

    monkeypatch.setattr(theme, "unicode_ok", lambda target: False)
    assert "* running implement" in _said(_run())


def test_sub_agents_are_shown_under_their_run_each_under_the_node_it_was_launched_from(
    platform: Platform,
) -> None:
    platform.listing([_run_entry("run-1")], [])
    platform.agents(
        "run-1",
        _node("run-1", None, "agent", tool_calls=1),
        _node("toolu_a", "run-1", "subagent", tool_calls=2),
        _node("toolu_b", "toolu_a", "subagent", tool="Task", tool_calls=1, status="completed"),
    )
    result = _run()
    assert result.exit_code == 0, result.output
    lines = [" ".join(line.strip("│├└─ ").split()) for line in ANSI.sub("", result.output).splitlines()]
    run = next(n for n, line in enumerate(lines) if line.startswith("● running implement"))
    assert lines[run + 1].startswith("● running sub-agent Agent · 2 tool calls 1m04s"), lines[run + 1]
    assert lines[run + 2].startswith("✓ done sub-agent Task · 1 tool call 1m04s"), lines[run + 2]
    indent = [len(line) - len(line.lstrip("│├└─ ")) for line in ANSI.sub("", result.output).splitlines()]
    assert indent[run + 2] > indent[run + 1] > indent[run]
    assert platform.asked == ["run-1"]


def test_a_flat_list_adds_no_sub_agent_line(platform: Platform) -> None:
    platform.listing([_run_entry("run-1")], [])
    platform.agents("run-1", _node("run-1", None, "agent", tool_calls=9))
    assert "sub-agent" not in _said(_run())


def test_a_run_whose_agents_cannot_be_read_is_still_shown(platform: Platform) -> None:
    platform.listing([_run_entry("run-1")], [])
    platform.agents("run-1", status=403)
    result = _run()
    assert result.exit_code == 0, result.output
    assert "● running implement" in _said(result) and "sub-agent" not in _said(result)


def test_the_live_view_reads_the_sub_agents_of_a_run_that_appears(platform: Platform) -> None:
    platform.listing([], [])
    platform.agents("run-new", _node("run-new", None, "agent"), _node("toolu_a", "run-new", "subagent"))
    platform.stream(("activity.upsert", _run_entry("run-new", work_item_key="FIN-20")))
    result = _run("--watch")
    assert result.exit_code == 0, result.output
    assert "sub-agent Agent · 0 tool calls" in _said(result)


def test_a_surface_never_reads_a_backends_private_meta() -> None:
    """The platform names the sub-agents (ADR 0154 §5): no CLI module reads `_meta` or a backend's key."""
    source = Path(__file__).parents[1] / "src" / "tranibor_cli"
    quoted = re.compile(r"""["'](_meta|claudeCode|parentToolUseId|toolName)["']""")
    offenders = [p.name for p in source.glob("*.py") if quoted.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
