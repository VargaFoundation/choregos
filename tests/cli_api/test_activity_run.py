# SPDX-License-Identifier: Apache-2.0
"""`choregos activity --run` against the real API (S67-03).

The CLI's respx tests prove what it draws from a journal it is handed. These prove the journal is the one
the API serves: the stored events, the run's context breakdown and its read right, over HTTP, with the CLI
signed in by the device flow.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from .conftest import Api, lay_run, project

FIXTURES = Path(__file__).parents[2] / "packages" / "cli" / "tests" / "fixtures"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _flat(text: str) -> str:
    return " ".join(ANSI.sub("", text).split())


def test_an_opencode_journal_read_through_the_api_gives_the_plan_and_the_calls(
    signed_in: Api, console: httpx.Client
) -> None:
    journal = json.loads((FIXTURES / "opencode-run-journal.json").read_text(encoding="utf-8"))
    lay_run(
        signed_in, project(console), "run-oc-0001", backend="opencode", status="succeeded", events=journal
    )
    shown = signed_in.choregos("activity", "--run", "run-oc-0001")
    assert shown.returncode == 0, shown.stdout + shown.stderr
    text = _flat(shown.stdout)
    assert "Plan" in text and "done" in text, text
    assert "29 tool calls" in text, text
    assert "Context: not measured" in text, text
    assert re.search(r"Plan \d+/\d+ done", text), text


def _breakdown(seq: int, call: int, tokens: int) -> dict[str, object]:
    return {
        "seq": seq,
        "type": "context.breakdown",
        "ts": "2026-11-10T09:00:30+00:00",
        "payload": {
            "call": call,
            "at": "2026-11-10T09:00:30+00:00",
            "model": "claude-sonnet",
            "prompt_tokens": tokens,
            "max_input_tokens": 200_000,
            "source": "gateway",
            "sections": {"system": tokens // 4, "messages": tokens - tokens // 4},
        },
    }


def test_a_claude_code_run_with_a_kept_breakdown_shows_its_context_and_json_is_the_same_journal(
    signed_in: Api, console: httpx.Client
) -> None:
    journal = json.loads((FIXTURES / "claude-code-run-journal.json").read_text(encoding="utf-8"))
    last = max(e["seq"] for e in journal)
    lay_run(
        signed_in,
        project(console),
        "run-cc-0001",
        backend="claude-code",
        status="succeeded",
        events=[*journal, _breakdown(last + 1, 3, 40_000)],
    )
    shown = signed_in.choregos("activity", "--run", "run-cc-0001")
    assert shown.returncode == 0, shown.stdout + shown.stderr
    text = _flat(shown.stdout)
    assert "Steps 5 tool calls · 4 finished · 2 updates of an unknown kind skipped" in text, text
    assert "Context call 3 · claude-sonnet · 40.0k of 200.0k (20%)" in text, text

    as_json = signed_in.choregos("activity", "--run", "run-cc-0001", "--json")
    assert as_json.returncode == 0, as_json.stdout + as_json.stderr
    body = json.loads(as_json.stdout)
    assert body["run"]["id"] == "run-cc-0001" and body["run"]["backend"] == "claude-code"
    assert len(body["steps"]) == 5 and body["unknown_updates"] == 2
    assert {step["status"] for step in body["steps"]} == {"completed", "failed", "pending"}
    assert [task["status"] for task in body["plan"]] == ["completed", "completed", "in_progress"]
    assert body["context"]["prompt_tokens"] == 40_000 and body["context"]["call"] == 3


def test_a_run_without_a_plan_or_a_breakdown_says_so_in_json_too(
    signed_in: Api, console: httpx.Client
) -> None:
    lay_run(signed_in, project(console), "run-new-0001", backend="claude-code", status="running")
    body = json.loads(signed_in.choregos("activity", "--run", "run-new-0001", "--json").stdout)
    assert body["plan"] is None and body["steps"] == [] and body["context"] is None
    assert "Context: not measured" in _flat(signed_in.choregos("activity", "--run", "run-new-0001").stdout)


def test_an_unknown_run_is_refused_by_the_api_and_said_plainly(signed_in: Api) -> None:
    refused = signed_in.choregos("activity", "--run", "run-nobody")
    assert refused.returncode != 0
    assert "404" in refused.stdout + refused.stderr, refused.stdout + refused.stderr
    assert "Traceback" not in refused.stdout + refused.stderr


def test_json_is_for_one_run_and_prints_once(signed_in: Api) -> None:
    for args in (("activity", "--json"), ("activity", "--run", "x", "--json", "--watch")):
        refused = signed_in.choregos(*args)
        assert refused.returncode != 0 and "--json" in refused.stdout + refused.stderr


def _call(seq: int, call_id: str, title: str, status: str) -> dict[str, object]:
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
        "ts": "2026-11-10T09:01:00+00:00",
        "payload": {"update": update},
    }


def test_watch_follows_a_run_in_flight_and_stops_when_it_ends(signed_in: Api, console: httpx.Client) -> None:
    """The run is in flight with one call; the CLI follows it; two more calls land, one while it runs and one
    as it ends: the CLI shows all three and exits by itself."""
    import subprocess
    import sys
    import time

    from .conftest import extend_run

    lay_run(
        signed_in,
        project(console),
        "run-live-001",
        backend="opencode",
        status="running",
        events=[_call(1, "call-a", "Read a.py", "completed")],
    )
    watcher = subprocess.Popen(
        [sys.executable, "-m", "tranibor_cli.main", "activity", "--run", "run-live-001", "--watch"],
        env=signed_in.env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(3)  # at least one poll has seen the run with a single call
    assert watcher.poll() is None, "the CLI stopped while the run was in flight"
    extend_run(signed_in, "run-live-001", [_call(2, "call-b", "Read b.py", "completed")])
    time.sleep(3)
    assert watcher.poll() is None
    extend_run(
        signed_in,
        "run-live-001",
        [_call(3, "call-c", "Read c.py", "completed")],
        status="succeeded",
    )
    out, _ = watcher.communicate(timeout=30)
    text = _flat(out)
    assert watcher.returncode == 0, out
    assert "3 tool calls · 3 finished" in text, text
    for title in ("Read a.py", "Read b.py", "Read c.py"):
        assert text.count(title) >= 1, text
