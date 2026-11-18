# SPDX-License-Identifier: Apache-2.0
"""The reduction of a run's ACP journal to its plan and steps (S67-03, ADR 0154 §5): no Rich, no network.

`fixtures/opencode-run-journal.json` is a REAL journal of an opencode run (the one the console's tests replay,
`apps/web/tests/fixtures/dev-run-opencode-events.json`). `fixtures/claude-code-run-journal.json` is NOT a
recording: it is built from the ACP schema (`session/update` kinds) and the shapes `claude-agent-acp` sends
(`_meta.claudeCode.toolName`, a `plan` from `TodoWrite`), because the repository holds no recorded claude-code
journal. It stays honest about that here and in the story's "does not prove".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from tranibor_cli.run_steps import RunSteps, clean

FIXTURES = Path(__file__).parent / "fixtures"


def _journal(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_a_claude_code_journal_reduces_to_its_plan_and_its_tool_calls() -> None:
    reduced = RunSteps.of(_journal("claude-code-run-journal.json"))
    assert [(s.id, s.kind, s.status, s.title) for s in reduced.steps.values()] == [
        ("toolu_01A", "read", "completed", "Read src/invoices/total.py"),
        ("toolu_02B", "think", "completed", "Explore how credit notes are stored"),
        ("toolu_03C", "edit", "completed", "Edit src/invoices/total.py"),
        ("toolu_04D", "execute", "failed", "`make test`"),
        ("toolu_05E", "execute", "pending", "`make test` [2J [31m"),
    ]
    # The whole plan replaces the earlier one: the latest revision is the plan.
    assert [(t.content, t.status, t.priority) for t in reduced.plan or []] == [
        ("Read the totals code", "completed", "high"),
        ("Deduct credit notes from the total", "completed", "high"),
        ("Add a regression test", "in_progress", "medium"),
    ]
    first = reduced.steps["toolu_01A"]
    assert (first.started_at, first.ended_at) == (
        "2026-11-10T09:00:01.200000Z",
        "2026-11-10T09:00:03.200000Z",
    )
    assert reduced.steps["toolu_05E"].ended_at is None
    assert reduced.finished() == 4


def test_an_unknown_update_kind_is_skipped_and_counted_but_a_known_non_step_is_not() -> None:
    reduced = RunSteps.of(_journal("claude-code-run-journal.json"))
    # Two kinds nobody knows; the message, thought and command-list chunks are known and not counted.
    assert reduced.unknown == 2
    assert len(reduced.steps) == 5


def test_a_sub_agent_tool_call_stays_flat_under_the_run_and_its_meta_is_not_read() -> None:
    """ADR 0154 §5: the platform names sub-agents, server-side; a surface never reads `_meta`."""
    reduced = RunSteps.of(_journal("claude-code-run-journal.json"))
    assert "toolu_02B" in reduced.steps
    assert not hasattr(reduced.steps["toolu_02B"], "sub_agent")
    source = Path(sys.modules[RunSteps.__module__].__file__ or "").read_text(encoding="utf-8")
    assert '"_meta"' not in source and "'_meta'" not in source and "claudeCode" not in source


def test_an_opencode_journal_reduces_to_its_calls_and_the_plan_its_todowrite_carries() -> None:
    reduced = RunSteps.of(_journal("opencode-run-journal.json"))
    assert len(reduced.steps) == 29
    assert reduced.finished() == 29
    assert reduced.unknown == 0
    assert reduced.last_seq == 242
    kinds: dict[str, int] = {}
    for step in reduced.steps.values():
        kinds[step.kind] = kinds.get(step.kind, 0) + 1
    assert kinds == {"execute": 12, "other": 8, "search": 3, "read": 3, "edit": 3}
    assert [t.content for t in reduced.plan or []] == [
        "Inspect invoice total code and tests",
        "Add failing test for credits deduction",
        "Fix compute_total credits handling",
        "Run make test and make lint",
        "Write and validate .choregos/result.json",
        "Commit the implementation",
    ]
    assert {t.status for t in reduced.plan or []} == {"completed"}
    # A title fills in on later updates: the call born as `bash` ends with its command as title.
    assert reduced.steps["call_Xcoo0OvT5yKVcBs5BuKzctMy"].title == "git status --short"


def test_the_end_of_a_call_is_its_first_finishing_update_and_an_event_counts_once() -> None:
    events = [
        {"seq": 1, "type": "session/update", "ts": "2026-11-10T09:00:00Z", "payload": {"update": {
            "sessionUpdate": "tool_call", "toolCallId": "t", "title": "x", "kind": "read"}}},
        {"seq": 2, "type": "session/update", "ts": "2026-11-10T09:00:04Z", "payload": {"update": {
            "sessionUpdate": "tool_call_update", "toolCallId": "t", "status": "completed"}}},
        {"seq": 3, "type": "session/update", "ts": "2026-11-10T09:00:09Z", "payload": {"update": {
            "sessionUpdate": "tool_call_update", "toolCallId": "t", "status": "completed"}}},
    ]  # fmt: skip
    reduced = RunSteps.of(events)
    reduced.apply(events)  # the stored journal and a later page overlap: nothing counts twice
    assert reduced.steps["t"].ended_at == "2026-11-10T09:00:04Z"
    assert len(reduced.steps) == 1
    journal = _journal("claude-code-run-journal.json")
    twice = RunSteps.of(journal)
    twice.apply(journal)
    assert twice.unknown == 2  # an overlapping page does not count the same update twice


def test_applying_later_events_continues_where_the_last_page_ended() -> None:
    journal = _journal("opencode-run-journal.json")
    reduced = RunSteps.of(journal[:60])
    assert reduced.last_seq == journal[59]["seq"]
    reduced.apply([e for e in journal if e["seq"] > reduced.last_seq])
    assert len(reduced.steps) == 29 and reduced.last_seq == 242


def test_an_orphan_update_creates_the_call_and_a_call_without_an_id_is_unknown() -> None:
    def event(seq: int, update: Any) -> dict[str, Any]:
        return {"seq": seq, "type": "session/update", "ts": "t", "payload": update}

    orphan = {"update": {"sessionUpdate": "tool_call_update", "toolCallId": "t9", "status": "completed"}}
    reduced = RunSteps.of(
        [
            event(1, orphan),
            event(2, {"update": {"sessionUpdate": "tool_call"}}),
            event(3, "not an object"),
        ]
    )
    assert [(s.id, s.title) for s in reduced.steps.values()] == [("t9", "t9")]
    assert reduced.unknown == 2


def test_a_cancelled_task_leaves_the_plan_and_a_duplicate_keeps_its_first_place() -> None:
    entries = [
        {"content": "a", "status": "cancelled"},
        {"content": "b", "status": "completed"},
        {"content": " b ", "status": "pending"},
        {"content": "c", "status": "weird", "priority": "odd"},
    ]
    update = {"sessionUpdate": "plan", "entries": entries}
    reduced = RunSteps.of([{"seq": 1, "type": "session/update", "ts": "t", "payload": {"update": update}}])
    assert [(t.content, t.status, t.priority) for t in reduced.plan or []] == [
        ("b", "completed", "medium"),
        ("c", "pending", "medium"),
    ]


def test_what_an_agent_wrote_never_carries_a_control_character() -> None:
    assert clean("a\x1b[2Jb\nc\x07") == "a [2Jb c"
