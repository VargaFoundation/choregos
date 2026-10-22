# SPDX-License-Identifier: Apache-2.0
"""A person talking to an agent directly (S41-09, ADR 0080 §10): the DTOs of `openapi.yaml`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto

SLUG = r"^[a-z0-9][a-z0-9-]{0,62}$"


class AgentInputMessage(Dto):
    role: Literal["user"] = "user"
    content: str = Field(min_length=1, max_length=65_536)


class AgentResponseCreate(Dto):
    """One turn of a conversation: a new session when `session_id` is absent, the next turn of it
    otherwise. The agent's own spec decides the model, the instructions and the tools."""

    #: The project the session works in: its budget pays, its rights bound the tools.
    project: str = Field(pattern=SLUG)
    input: str | list[AgentInputMessage] = Field(min_length=1)
    session_id: str | None = None
    #: Answer as server-sent events rather than one JSON body.
    stream: bool = False
    #: A cap of the caller's on the whole session; the project's budgets still apply. Only when it opens.
    max_usd: float | None = Field(default=None, gt=0, le=100)


class AgentOutputText(Dto):
    type: Literal["output_text"] = "output_text"
    text: str


class AgentOutputMessage(Dto):
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: list[AgentOutputText]


class AgentResponseError(Dto):
    #: `budget_exceeded`, `model_error`, `max_turns`, `tool_calls_exceeded`.
    code: str
    message: str


class AgentResponseUsage(Dto):
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    tool_calls: int = 0


class AgentResponse(Dto):
    """The Responses API's shape, the part a client reads."""

    id: str
    object: Literal["response"] = "response"
    session_id: str
    status: Literal["completed", "failed"]
    output: list[AgentOutputMessage] = Field(default_factory=list)
    output_text: str = ""
    usage: AgentResponseUsage = Field(default_factory=AgentResponseUsage)
    error: AgentResponseError | None = None


class AgentDelta(Dto):
    """A `response.output_text.delta` event: the whole text in one delta (no token streaming yet)."""

    type: Literal["response.output_text.delta"] = "response.output_text.delta"
    response_id: str
    delta: str


class AgentSessionEventDto(Dto):
    seq: int
    type: str
    ts: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentSession(Dto):
    id: str
    agent: str
    agent_version: int
    project: str
    status: Literal["open", "closed", "failed"]
    reason: str | None = None
    model: str
    responses: int
    tool_calls: int
    cost_usd: float
    created_at: datetime
    expires_at: datetime
    closed_at: datetime | None = None
    events: list[AgentSessionEventDto] = Field(default_factory=list)


# ───────────── a hosted session's pod, on the internal API (S41-11) ─────────────


class AgentSessionTurn(Dto):
    """A response the person asked for, waiting for the session's pod."""

    turn_id: str
    input: str
    #: How the pod sends it to the container: `POST /responses` or `POST /invocations`.
    protocol: Literal["responses", "invocations"]


class AgentSessionPodEvent(Dto):
    """What the pod's runner saw during a turn: the container's model calls refused, its tool calls."""

    type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentSessionTurnResult(Dto):
    """How the container answered a turn; posted once per turn, a second post changes nothing."""

    #: `answered`, or why it did not: `failed`, `not_ready`, `timeout`, `max_turns`, `budget_exceeded`.
    status: Literal["answered", "failed", "not_ready", "timeout", "max_turns", "budget_exceeded"]
    text: str = Field(default="", max_length=200_000)
    error: str | None = Field(default=None, max_length=4000)
    response_id: str | None = Field(default=None, max_length=200)
    turns: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    events: list[AgentSessionPodEvent] = Field(default_factory=list, max_length=200)


class AgentSessionToolCall(Dto):
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentSessionToolAnswer(Dto):
    #: What the model reads back: the courtier's text, or why the call was refused.
    text: str


class AgentSessionTools(Dto):
    #: Each as MCP's `tools/list` announces it: `name`, `description`, `inputSchema`.
    tools: list[dict[str, Any]] = Field(default_factory=list)
