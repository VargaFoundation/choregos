# SPDX-License-Identifier: Apache-2.0
"""A scriptable fake outpost (ADR 0005): what the daemon `choregos-outpost` does with a call, and,
for a platform that has no door to meet it at, the rendezvous itself, in memory.

It has two sides, and a test picks the one it needs.

- **The daemon's side**, `make`: handed a call as the daemon reads one once the envelope is open
  (`outpost-call.schema.json`), it decides what an outpost decides alone, from a policy the platform
  cannot write (ADR 0147 §6): the alias exists, the tool is inside the alias's patterns, the
  arguments weigh no more than its ceiling, and a tool its server does not declare read-only is a
  write, refused unless the alias allows writes. Then it calls the alias's MCP server and reports
  what the contract's answer says (`outpost-answer.schema.json`). Whoever stands between it and the
  platform carries the call and the answer: the outposts' door in the platform's own tests, where a
  scripted daemon polls, opens, hands the call here and seals what comes back.
- **The platform's side**, the port `choregos_adapters.outpost.port.Rendezvous`: `place`, `wait`,
  `cancel`, `forget`, in memory. It is what `CHOREGOS_FAKES=1` gives a connector of type
  `mcp-outpost` on a deployment that has no key to seal a call's arguments under: no row, no door,
  the same refusals.

Scripted: `go_offline` and `come_back`, `pause` and `resume`, `refuse_next` (the outpost's own
policy refuses the next call), `lose_lease` (the next call is taken and never answered: executed or
not, the platform does not know), `answer_next` (the next call's outcome: `unanswered`, `oversized`,
or a server's `error`). Read back: `received` (every call it was handed), `placed` (every call the
platform placed at its rendezvous), `cancellations` (those it stopped waiting for), and each
server's own `calls`.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from choregos_contracts import (
    OutpostAnswer,
    OutpostAnswerOutcome,
    OutpostCallKind,
    OutpostCallState,
    OutpostRefusalCode,
)

from ..outpost import port
from .mcp import FakeMcpServer


@dataclass(frozen=True)
class Made:
    """What the outpost did with a call: an answer (the contract's `OutpostAnswer`, as JSON), a
    refusal (one of the contract's codes), or neither when the lease is lost and nothing is sent."""

    answer: dict[str, Any] | None = None
    refusal: str | None = None

    @property
    def sent(self) -> bool:
        return self.answer is not None or self.refusal is not None


@dataclass
class _Placed:
    call: port.Call
    write: bool
    outcome: port.Outcome | None = None


def _weight(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8"))


@dataclass
class FakeOutpost:
    """One outpost: its policy file (`endpoints`, `tools`, `writes_allowed`, `max_payload_bytes`),
    what it was scripted to do next, and what it saw."""

    #: The outpost's name, as refusals say it.
    name: str = "fake-outpost"
    #: The MCP server behind each alias of the policy file.
    endpoints: dict[str, FakeMcpServer] = field(default_factory=dict)
    #: The server of an alias `endpoints` does not list: with it, every alias exists.
    fallback: Callable[[], FakeMcpServer] | None = None
    #: The tool-name patterns of each alias (`tools` in the policy file); every tool when not listed.
    tools: dict[str, list[str]] = field(default_factory=dict)
    #: The aliases that allow writes (`writes: allow`); every other denies them.
    writes_allowed: set[str] = field(default_factory=set)
    #: The most a call's arguments may weigh, in bytes (`max_payload_kb`).
    max_payload_bytes: int = 256 * 1024
    #: Every call it was handed, as it read it.
    received: list[dict[str, Any]] = field(default_factory=list)
    #: Every call placed at its own rendezvous.
    placed: list[port.Call] = field(default_factory=list)
    #: The calls the platform said it stopped waiting for.
    cancellations: list[str] = field(default_factory=list)
    offline: bool = False
    paused: bool = False
    _refusals: list[str] = field(default_factory=list)
    _lost: int = 0
    _outcomes: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    _calls: dict[str, _Placed] = field(default_factory=dict)

    # ───────────────────────── scripting ─────────────────────────

    def go_offline(self) -> None:
        """No instance polls any more: nothing is claimed."""
        self.offline = True

    def come_back(self) -> None:
        self.offline = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False

    def refuse_next(self, code: str = "tool_not_allowed") -> None:
        """The outpost's own policy refuses the next call with this code: its server receives nothing."""
        self._refusals.append(OutpostRefusalCode(code).value)

    def lose_lease(self, calls: int = 1) -> None:
        """The next call is taken, made at the server, and never answered: its lease runs out."""
        self._lost += calls

    def answer_next(self, outcome: str, **carried: Any) -> None:
        """The next tool call's outcome, in place of the server's result: `unanswered`, `oversized`,
        or `error` with the server's `message` and `code`."""
        self._outcomes.append((OutpostAnswerOutcome(outcome).value, carried))

    @property
    def aliases(self) -> list[str]:
        """The aliases an instance of this outpost announces at each poll."""
        return sorted(self.endpoints)

    # ───────────────────────── the daemon's side ─────────────────────────

    def _server(self, alias: str) -> FakeMcpServer | None:
        if alias in self.endpoints:
            return self.endpoints[alias]
        return self.fallback() if self.fallback is not None else None

    def _lets_through(self, alias: str, tool: str) -> bool:
        return any(fnmatch.fnmatchcase(tool, pattern) for pattern in self.tools.get(alias, ["*"]))

    async def make(self, call: Mapping[str, Any]) -> Made:
        """What the outpost does with one call it opened. Its server is called from the outpost's
        own network, under a policy that opens the alias's host and no other, as the daemon does: it
        is not an egress of the platform's, whatever policy the caller happens to be under."""
        from choregos_core.egress import AllowedRule, EgressPolicy, use_policy

        alias = str(call.get("endpoint") or "")
        host = f"{alias or 'unknown'}.outpost.test"
        only = EgressPolicy(rules=(AllowedRule(host=host, ports=(80,), used_by=("api",)),), mode="enforce")
        with use_policy(only, origin=f"outpost:{alias}"):
            return await self._make(call, f"http://{host}/mcp")

    async def _make(self, call: Mapping[str, Any], url: str) -> Made:
        from ..mcp import McpClient, McpError

        self.received.append(dict(call))
        call_id, kind, alias = str(call["id"]), str(call.get("kind")), str(call.get("endpoint") or "")
        if self._refusals:
            return Made(refusal=self._refusals.pop(0))
        if kind not in set(OutpostCallKind):
            return Made(refusal=OutpostRefusalCode.UNSUPPORTED_KIND.value)
        server = self._server(alias)
        if server is None:
            return Made(refusal=OutpostRefusalCode.UNKNOWN_ENDPOINT.value)
        client = McpClient(url, token=server.token, transport=server.transport())
        try:
            listed = await client.list_tools()
        except McpError:
            return Made(refusal=OutpostRefusalCode.ENDPOINT_UNAVAILABLE.value)
        if kind == OutpostCallKind.LIST_TOOLS.value:
            tools = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                    "read_only": tool.read_only,
                    "output_schema": tool.output_schema,
                }
                for tool in listed
                if self._lets_through(alias, tool.name)
            ]
            return self._unless_lost({"id": call_id, "outcome": "result", "tools": tools})
        name, arguments = str(call.get("tool") or ""), dict(call.get("arguments") or {})
        if not self._lets_through(alias, name):
            return Made(refusal=OutpostRefusalCode.TOOL_NOT_ALLOWED.value)
        if _weight(arguments) > self.max_payload_bytes:
            return Made(refusal=OutpostRefusalCode.PAYLOAD_TOO_LARGE.value)
        # Whether the tool writes is read from the outpost's own server, never from the call.
        write = not any(tool.name == name and tool.read_only for tool in listed)
        if write and alias not in self.writes_allowed:
            return Made(refusal=OutpostRefusalCode.WRITE_DENIED.value)
        answer: dict[str, Any] = {"id": call_id, "write": write, "duration_ms": 0}
        try:
            result = await client.call_tool(name, arguments)
        except McpError as error:
            code = error.code if isinstance(error.code, int) else None
            answer |= {"outcome": "error", "error": {"message": str(error)[:2000], "code": code}}
            return self._unless_lost(answer)
        if self._outcomes:
            outcome, carried = self._outcomes.pop(0)
            answer["outcome"] = outcome
            if outcome == OutpostAnswerOutcome.ERROR.value:
                answer["error"] = {
                    "message": str(carried.get("message", "refused")),
                    "code": carried.get("code"),
                }
            return self._unless_lost(answer)
        return self._unless_lost(answer | {"outcome": "result", "result": result})

    def _unless_lost(self, answer: dict[str, Any]) -> Made:
        if self._lost:
            self._lost -= 1
            return Made()
        return Made(answer=answer)

    # ───────────────────────── the platform's side: the rendezvous, in memory ─────────────────────────

    @property
    def outpost(self) -> str:
        return self.name

    async def place(self, call: port.Call) -> str:
        if self.paused:
            raise port.NotAdmitted(port.PAUSED, f"the outpost `{self.name}` is paused: it is handed nothing")
        write = call.write_key is not None
        call_id = (
            hashlib.sha256(f"{self.name}\x1f{call.write_key}".encode()).hexdigest()
            if write
            else uuid.uuid4().hex
        )
        if call_id in self._calls:
            # The same key is the same call: nothing is placed, and nothing is delivered, a second time.
            return call_id
        if self.offline:
            raise port.NotAdmitted(
                port.OFFLINE,
                f"the outpost `{self.name}` is offline: no instance of it polled lately, and nothing "
                "reached its server",
            )
        self.placed.append(call)
        self._calls[call_id] = _Placed(call=call, write=write)
        return call_id

    async def wait(self, call_id: str) -> port.Outcome | None:
        placed = self._calls.get(call_id)
        if placed is None:
            return None
        if placed.outcome is None:
            placed.outcome = await self._outcome_of(call_id, placed)
        return placed.outcome

    async def _outcome_of(self, call_id: str, placed: _Placed) -> port.Outcome:
        call = placed.call
        made = await self.make(
            {
                "id": call_id,
                "kind": call.kind.value,
                "endpoint": call.endpoint,
                "tool": call.tool,
                "arguments": call.arguments,
                "asserted": dict(call.asserted),
            }
        )
        instance, at = f"{self.name}-0", datetime.now(UTC)
        if made.refusal is not None:
            return port.Outcome(
                OutpostCallState.REFUSED, write=placed.write, refusal=OutpostRefusalCode(made.refusal)
            )
        answer = OutpostAnswer.model_validate(made.answer) if made.answer is not None else None
        if answer is None or answer.outcome is OutpostAnswerOutcome.UNANSWERED:
            lost = OutpostCallState.IN_DOUBT if placed.write else OutpostCallState.TIMED_OUT
            return port.Outcome(lost, write=placed.write, instance_id=instance, claimed_at=at)
        return port.Outcome(
            OutpostCallState.ANSWERED, write=placed.write, answer=answer, instance_id=instance, claimed_at=at
        )

    async def cancel(self, call_id: str) -> None:
        if call_id in self._calls:
            self.cancellations.append(call_id)

    async def forget(self, call_id: str) -> None:
        placed = self._calls.get(call_id)
        if placed is not None and placed.outcome is not None and placed.outcome.answer is not None:
            kept = placed.outcome
            placed.outcome = port.Outcome(
                kept.state, write=kept.write, instance_id=kept.instance_id, claimed_at=kept.claimed_at
            )


__all__ = ["FakeOutpost", "Made"]
