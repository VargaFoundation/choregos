# SPDX-License-Identifier: Apache-2.0
"""The agents of a run, derived from its journal (ADR 0154 §5, G5).

A sub-agent launched inside a sandboxed run appears in the journal only as ACP `tool_call` updates: ACP has no
notion of a sub-agent. What says "this call delegated" and "this call is the sub-agent's own" is private to each
backend, so the platform keeps one **rule per ACP backend** (`RULES`, one entry for each backend of
`choregos_runner.backends`, kept equal by a test) and derives the nodes here, server-side. A surface never
parses a backend's private shapes (its `_meta`): it reads the nodes.

A node is the run's own agent (`kind="agent"`, the root) or a sub-agent (`kind="subagent"`), which is a call the
backend's rule names as delegating **when that backend also sends the parent id of the calls it makes**. A
backend that sends none gives a flat list: its delegating call stays an ordinary tool call of the run, and no
sub-agent is made of it (never guessed). A call whose parent id names no known delegating call is the root's.

Tokens and cost stay per run (the gateway counts per run key): a node carries no spend.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

AgentKind = Literal["agent", "subagent"]

#: The journal's type for one ACP `session/update`.
SESSION_UPDATE = "session/update"
#: ACP's tool-call statuses, as the journal holds them.
CALL_STATUSES = ("pending", "in_progress", "completed", "failed")


@dataclass(frozen=True, slots=True)
class SubagentRule:
    """How one ACP backend names a delegating tool call and the parent of the calls made inside it.

    Each path is a chain of keys read in a `session/update`'s `update` object. `None`: the backend says
    nothing there.
    """

    #: The delegating tools' names, compared lower-cased (Claude Code's `Task` and `Agent`, opencode's `task`).
    tools: frozenset[str]
    #: Where the backend puts the tool's programmatic name; `None` reads the call's `title`, which is the tool's
    #: name for a backend that sets no other (opencode).
    name_path: tuple[str, ...] | None
    #: Where the backend puts the id of the delegating call a call was made inside; `None`: it sends none.
    parent_path: tuple[str, ...] | None
    #: The adapter and version the rule was read from, and the file that sends the parent id.
    source: str


#: One entry per ACP backend the runner image carries. `None`: the backend delegates to nothing the journal can
#: tell (it has no sub-agent tool, or the shape is unknown), so its calls are a flat list.
RULES: dict[str, SubagentRule | None] = {
    # `@agentclientprotocol/claude-agent-acp` 0.85.1: the first `tool_call` of a call carries
    # `_meta.claudeCode.toolName` (dist/tool-calls/renderer.js, `toolUseMeta`), and every update of a call made
    # inside a sub-agent carries `_meta.claudeCode.parentToolUseId` = the delegating call's id
    # (dist/acp-agent.js, `stampParentToolUseId`). The client does not announce the `subagents` capability,
    # so the adapter keeps them in the one session (dist/native-subagents.js is then not in the path).
    "claude-code": SubagentRule(
        tools=frozenset({"task", "agent"}),
        name_path=("_meta", "claudeCode", "toolName"),
        parent_path=("_meta", "claudeCode", "parentToolUseId"),
        source="@agentclientprotocol/claude-agent-acp 0.85.1, dist/acp-agent.js stampParentToolUseId",
    ),
    # opencode 1.18.31 `opencode acp`: the `task` tool is a `tool_call` titled `task` (kind `think`); the
    # sub-agent's own tool calls live in a child session the adapter never registered, so
    # `handlePartUpdated` drops them (`if (!session) return`), and no update carries `_meta`. No parent id:
    # the delegating call is an ordinary call and no sub-agent is derived.
    "opencode": SubagentRule(
        tools=frozenset({"task"}),
        name_path=None,
        parent_path=None,
        source="opencode 1.18.31, packages/opencode/src/acp/event.ts handlePartUpdated",
    ),
    # No delegating tool known at the pinned version: a flat list.
    "codex": None,
    "gemini-cli": None,
    "goose": None,
    "copilot-cli": None,
    # The platform's own runtimes: no ACP CLI, no sub-agent.
    "code": None,
    "prompt": None,
    "hosted": None,
    "remote": None,
}


@dataclass(frozen=True, slots=True)
class AgentNode:
    """One agent of a run: the run's own agent, or a sub-agent it delegated to."""

    id: str
    parent: str | None
    kind: AgentKind
    #: The delegating tool's name (`Task`, `Agent`, `task`); `None` for the run's own agent.
    tool: str | None
    #: An ACP status (`pending`, `in_progress`, `completed`, `failed`); the root's is the caller's to set.
    status: str | None
    started: str | None
    ended: str | None
    #: The tool calls this agent made itself, a delegating call excluded.
    tool_calls: int


def _at(update: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = update
    for key in path:
        if not isinstance(node, Mapping):
            return None
        node = node.get(key)
    return node


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(slots=True)
class _Call:
    id: str
    name: str | None
    parent: str | None
    status: str | None
    first: str | None
    last: str | None


def _calls(events: Iterable[Mapping[str, Any]], rule: SubagentRule | None) -> list[_Call]:
    by_id: dict[str, _Call] = {}
    for event in sorted(events, key=lambda e: int(e.get("seq") or 0)):
        if event.get("type") != SESSION_UPDATE:
            continue
        payload = event.get("payload")
        update = payload.get("update") if isinstance(payload, Mapping) else None
        if not isinstance(update, Mapping):
            continue
        if update.get("sessionUpdate") not in ("tool_call", "tool_call_update"):
            continue
        call_id = _text(update.get("toolCallId"))
        if call_id is None:
            continue
        ts = event.get("ts")
        stamp = ts.isoformat() if hasattr(ts, "isoformat") else _text(ts)
        call = by_id.get(call_id)
        if call is None:
            call = by_id[call_id] = _Call(call_id, None, None, None, stamp, stamp)
        call.last = stamp or call.last
        status = update.get("status")
        if status in CALL_STATUSES:
            call.status = status
        if rule is None:
            continue
        # A later update of a call may lose what the first one carried: the first value is kept.
        if call.name is None:
            call.name = _text(_at(update, rule.name_path)) if rule.name_path else _text(update.get("title"))
        if call.parent is None and rule.parent_path:
            call.parent = _text(_at(update, rule.parent_path))
    return list(by_id.values())


def agent_nodes(
    backend: str,
    events: Iterable[Mapping[str, Any]],
    *,
    root_id: str,
    root_status: str | None = None,
    root_started: str | None = None,
    root_ended: str | None = None,
) -> list[AgentNode]:
    """The run's agents: the root first, then each sub-agent in the order it was launched.

    `events` are the journal's `{seq, type, ts, payload}` rows; a backend with no rule gives the root alone.
    """
    rule = RULES.get(backend)
    calls = _calls(events, rule)
    delegating: dict[str, _Call] = {}
    if rule is not None and rule.parent_path is not None:
        delegating = {c.id: c for c in calls if c.name is not None and c.name.lower() in rule.tools}
    owned = {node_id: 0 for node_id in (root_id, *delegating)}
    for call in calls:
        if call.id in delegating:
            continue
        owner = call.parent if call.parent in delegating else root_id
        owned[owner] += 1
    nodes = [AgentNode(root_id, None, "agent", None, root_status, root_started, root_ended, owned[root_id])]
    for call in delegating.values():
        finished = call.status in ("completed", "failed")
        nodes.append(
            AgentNode(
                id=call.id,
                parent=call.parent if call.parent in delegating else root_id,
                kind="subagent",
                tool=call.name,
                status=call.status,
                started=call.first,
                ended=call.last if finished else None,
                tool_calls=owned[call.id],
            )
        )
    return nodes
