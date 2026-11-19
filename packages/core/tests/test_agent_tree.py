"""S67-12: the agents of a run, derived from its journal with a per-backend rule (ADR 0154 §5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tranibor_core.agent_tree import RULES, SubagentRule, agent_nodes

FIXTURES = Path(__file__).parent / "fixtures" / "agent_tree"


def _events(name: str, *, recorded: bool = False) -> list[dict[str, Any]]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    # A fixture says in its `source` whether a live run produced it; the claim is checked both ways, so a
    # built fixture cannot pass for a recording nor the other way round.
    assert data["source"].startswith("RECORDED") is recorded, data["source"][:60]
    events: list[dict[str, Any]] = data["events"]
    return events


def test_claude_code_sub_agents_are_nodes_with_their_own_calls() -> None:
    nodes = {
        n.id: n for n in agent_nodes("claude-code", _events("claude-code-subagent.json"), root_id="run-1")
    }
    assert list(nodes) == ["run-1", "toolu_agent_1", "toolu_task_2", "toolu_agent_2"]
    root, first, nested, second = nodes.values()
    assert (root.kind, root.parent_id, root.tool, root.tool_calls) == ("agent", None, None, 1)
    assert (first.kind, first.parent_id, first.tool, first.status) == (
        "subagent",
        "run-1",
        "Agent",
        "completed",
    )
    # The grep and the read (whose last update lost the parent id) are the first sub-agent's own.
    assert first.tool_calls == 2
    assert (nested.parent_id, nested.tool, nested.tool_calls) == ("toolu_agent_1", "Task", 1)
    assert first.started_at == "2026-11-16T10:00:03.000000Z"
    assert first.ended_at == "2026-11-16T10:00:12.000000Z"
    assert (second.status, second.ended_at, second.tool_calls) == ("running", None, 0)


def test_opencode_gives_a_flat_list_and_no_sub_agent() -> None:
    """On a journal RECORDED from a live opencode run that delegated (S67-12 finish): the `task` call is
    there, the sub-agent's own calls are not, and the platform's `/runs/{id}/agents` said 6 calls."""
    events = _events("opencode-task.json", recorded=True)
    updates = [e["payload"]["update"] for e in events if e["type"] == "session/update"]
    task = next(u for u in updates if u["sessionUpdate"] == "tool_call" and u["title"] == "task")
    assert task["kind"] == "think"
    # What the rule is read from: the real adapter sends no `_meta` and no parent id, and the delegating
    # call finishes carrying the child session's answer.
    assert not any("_meta" in u or "parentToolUseId" in json.dumps(u) for u in updates)
    assert any(
        u.get("toolCallId") == task["toolCallId"] and "<task_result>" in json.dumps(u.get("rawOutput"))
        for u in updates
    )
    nodes = agent_nodes("opencode", events, root_id="run-1")
    assert [(n.id, n.kind) for n in nodes] == [("run-1", "agent")]
    # The `task` call is an ordinary call of the run, never guessed into a sub-agent.
    assert nodes[0].tool_calls == 6


def test_a_backend_that_names_a_delegating_tool_but_sends_no_parent_id_is_flat() -> None:
    """The guard: the same journal under a rule without a parent path gives no sub-agent."""
    events = _events("claude-code-subagent.json")
    RULES["flat-claude"] = SubagentRule(
        tools=frozenset({"task", "agent"}),
        name_path=("_meta", "claudeCode", "toolName"),
        parent_path=None,
        source="test",
    )
    try:
        nodes = agent_nodes("flat-claude", events, root_id="run-1")
    finally:
        del RULES["flat-claude"]
    assert [n.kind for n in nodes] == ["agent"]
    assert nodes[0].tool_calls == 7


def test_a_parent_id_that_names_no_delegating_call_is_the_roots() -> None:
    events = [
        {
            "seq": 1,
            "type": "session/update",
            "ts": "2026-11-16T10:00:01Z",
            "payload": {
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": "c1",
                    "status": "pending",
                    "_meta": {"claudeCode": {"toolName": "Read", "parentToolUseId": "unknown"}},
                }
            },
        }
    ]
    nodes = agent_nodes("claude-code", events, root_id="r")
    assert [(n.id, n.tool_calls) for n in nodes] == [("r", 1)]


def test_a_backend_without_a_rule_and_an_unknown_backend_give_the_root_alone() -> None:
    events = _events("claude-code-subagent.json")
    for backend in ("codex", "no-such-backend"):
        assert [n.id for n in agent_nodes(backend, events, root_id="r")] == ["r"]
