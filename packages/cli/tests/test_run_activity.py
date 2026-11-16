# SPDX-License-Identifier: Apache-2.0
"""`choregos activity --run ID` (S67-03, ADR 0154 §2-3, §9): a run's plan, tool calls and context.

The API is faked with respx; the run and the breakdown it serves are validated against `openapi.yaml`.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
V1 = f"{API}/api/v1"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 10, 9, 0, 25, tzinfo=UTC)
RUN = "0192a3b4c5d6e7f8"


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_cli import activity, run_activity, theme
    from choregos_cli import client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(theme, "MONOCHROME", False)
    monkeypatch.setattr(run_activity, "POLL_EVERY_S", 0.01)
    monkeypatch.setattr(run_activity, "clock", lambda: NOW)
    monkeypatch.setattr(activity, "clock", lambda: NOW)
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_TOKEN", "chg_test")
    monkeypatch.setenv("CHOREGOS_ORG", "acme")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


def _journal(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


class Platform:
    def __init__(self, router: respx.Router, contract: Any) -> None:
        self.router, self.contract = router, contract
        self.run(status="succeeded")
        self.events(_journal("claude-code-run-journal.json"))
        self.context(None)

    def run(self, **changes: Any) -> None:
        body = {"id": RUN, "status": "running", "stage_role": "implement", "backend": "claude-code"} | changes
        self.contract("RunSummary", body)
        self.router.get(f"{V1}/runs/{RUN}").mock(return_value=Response(200, json=body))

    def events(self, journal: list[dict[str, Any]]) -> None:
        for event in journal:
            self.contract("RunEvent", event)

        def serve(request: Any) -> Response:
            after = int(request.url.params.get("after_seq", 0))
            return Response(200, json=[e for e in journal if e["seq"] > after])

        self.listed = self.router.get(f"{V1}/runs/{RUN}/events").mock(side_effect=serve)

    def context(self, latest: dict[str, Any] | None, *, measurable: bool = True) -> None:
        body = {
            "run_id": RUN,
            "measurable": measurable,
            "backend": "claude-code",
            "latest": latest,
            "series": [],
        }
        self.contract("RunContext", body)
        self.router.get(f"{V1}/runs/{RUN}/context-breakdown").mock(return_value=Response(200, json=body))


@pytest.fixture
def platform(contract: Any) -> Any:
    with respx.mock(assert_all_called=False) as router:
        yield Platform(router, contract)


def _run(*words: str, env: dict[str, str] | None = None) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, ["activity", "--run", RUN, *words], env=env)


def _said(result: Any) -> str:
    return " ".join(ANSI.sub("", result.output).split())


def _lines(result: Any) -> list[str]:
    return [" ".join(line.strip("│╭╮╰╯─ ").split()) for line in ANSI.sub("", result.output).splitlines()]


def test_a_claude_code_run_shows_its_plan_its_calls_with_state_and_time_and_the_context(
    platform: Platform,
) -> None:
    platform.context(
        {
            "call": 7,
            "at": "2026-11-10T09:00:20Z",
            "model": "anthropic/sonnet-4.5",
            "prompt_tokens": 40_000,
            "max_input_tokens": 200_000,
            "sections": {"system": 4_000, "messages": 30_000, "tools.mcp": 6_000},
        }
    )
    result = _run()
    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert "● running" not in " ".join(lines)  # the run in the fake has succeeded
    assert any(line.startswith("✓ done implement") for line in lines), lines
    assert "Plan 2/3 done" in lines
    assert "✓ Read the totals code" in lines and "● Add a regression test" in lines
    assert "Steps 5 tool calls · 4 finished · 2 updates of an unknown kind skipped" in lines
    assert "✓ done 2s read Read src/invoices/total.py" in lines
    assert "✗ failed 5s execute `make test`" in lines
    assert any(line.startswith("○ queued 10s execute `make test`") for line in lines)  # ran to the pinned now
    assert "Context call 7 · sonnet-4.5 · 40.0k of 200.0k (20%)" in lines
    messages = next(line for line in lines if line.startswith("messages"))
    assert messages.endswith("75%") and "███████████████░░░░░" in messages
    assert [line.split()[0] for line in lines if re.match(r"(system|messages|tools\.mcp) ", line)] == [
        "messages",
        "tools.mcp",
        "system",
    ]


def test_an_opencode_run_shows_its_steps_and_the_plan_its_todowrite_carried(platform: Platform) -> None:
    platform.events(_journal("opencode-run-journal.json"))
    platform.run(backend="opencode")
    lines = _lines(_run())
    assert "Plan 6/6 done" in lines
    assert "Steps 29 tool calls · 29 finished" in lines
    assert "… 0 earlier" not in " ".join(lines)
    assert any(line.endswith("execute git status --short") for line in lines)


def test_only_the_latest_steps_are_listed_and_the_earlier_ones_counted(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_cli import run_activity

    monkeypatch.setattr(run_activity, "STEPS_SHOWN", 5)
    platform.events(_journal("opencode-run-journal.json"))
    lines = _lines(_run())
    assert "… 24 earlier" in lines
    assert (
        sum(1 for line in lines if line.startswith("✓ done")) == 1 + 5
    )  # the run's own line, then five steps


def test_a_run_with_no_breakdown_says_not_measured(platform: Platform) -> None:
    assert "Context: not measured" in _said(_run())


def test_a_backend_the_platform_cannot_see_into_says_not_measured_even_with_a_breakdown_left_over(
    platform: Platform,
) -> None:
    platform.context(
        {
            "call": 1,
            "at": "2026-11-10T09:00:20Z",
            "model": "m",
            "prompt_tokens": 100,
            "sections": {"messages": 100},
        },
        measurable=False,
    )
    assert "Context: not measured" in _said(_run())


def test_an_unknown_run_is_a_red_line(platform: Platform) -> None:
    platform.router.get(f"{V1}/runs/{RUN}").mock(
        return_value=Response(404, json={"title": "Not found", "detail": "Run not found"})
    )
    result = _run()
    assert result.exit_code == 1 and "Run not found" in _said(result)


def test_run_takes_none_of_the_tree_filters(platform: Platform) -> None:
    result = _run("--project", "billing-api")
    assert result.exit_code == 1 and "--run shows one run" in _said(result)


def test_an_agent_cannot_paint_the_terminal_with_a_title(platform: Platform) -> None:
    result = _run(env={"FORCE_COLOR": "1", "TTY_COMPATIBLE": "1", "NO_COLOR": "1"})
    assert "\x1b[2J" not in result.output and "\x1b[31m" not in result.output
    assert "`make test` [2J [31m" in _said(result)


def test_watch_reads_only_what_is_new_and_stops_when_the_run_ends(platform: Platform) -> None:
    journal = _journal("claude-code-run-journal.json")
    first, rest = [e for e in journal if e["seq"] <= 8], [e for e in journal if e["seq"] > 8]
    served = {"events": first}

    def serve(request: Any) -> Response:
        after = int(request.url.params.get("after_seq", 0))
        sent = [e for e in served["events"] if e["seq"] > after]
        served["events"] = first + rest  # what arrives while the person watches
        return Response(200, json=sent)

    platform.router.get(f"{V1}/runs/{RUN}/events").mock(side_effect=serve)
    states = iter(["running", "running", "succeeded"])

    def run(request: Any) -> Response:
        body = {"id": RUN, "status": next(states, "succeeded"), "stage_role": "implement"}
        return Response(200, json=body)

    platform.router.get(f"{V1}/runs/{RUN}").mock(side_effect=run)
    result = _run("--watch")
    assert result.exit_code == 0, result.output
    assert "Steps 5 tool calls" in _said(result)
    asked = [
        r.request.url.params.get("after_seq")
        for r in platform.router.calls
        if r.request.url.path.endswith("/events")
    ]
    assert asked[:2] == ["0", "8"], (
        asked
    )  # then what comes after the last number read, never the whole journal
    assert len(asked) == 3  # initial read, one poll while running, the last one when the run has ended


def test_no_color_gives_no_escape_sequence_and_keeps_the_words(platform: Platform) -> None:
    forced = {"FORCE_COLOR": "1", "TTY_COMPATIBLE": "1"}
    assert ANSI.search(_run(env=forced).output), (
        "the forced colour run must colour, or the test proves nothing"
    )
    plain = _run(env=forced | {"NO_COLOR": "1"})
    assert plain.exit_code == 0 and not ANSI.search(plain.output)
    assert "✗ failed" in _said(plain) and "Context: not measured" in _said(plain)


def test_an_ascii_terminal_gets_ascii_glyphs_and_bars(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_cli import theme

    platform.context(
        {
            "call": 1,
            "at": "2026-11-10T09:00:20Z",
            "model": "m",
            "prompt_tokens": 100,
            "sections": {"messages": 100},
        }
    )
    monkeypatch.setattr(theme, "unicode_ok", lambda target: False)
    said = _said(_run())
    assert "x failed 5s execute `make test`" in said and "+ done 2s read" in said
    assert "#" * 20 in said and "█" not in said
