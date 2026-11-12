# SPDX-License-Identifier: Apache-2.0
"""Agent sessions and their events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class AgentSessionRecord(Base, PkMixin, TimestampMixin):
    """A person talking to an agent directly (S41-09, ADR 0080 §10), next to runs: no work item, no
    workflow. Its gateway key is minted once, when it opens, sealed in the secret store, and revoked
    when it closes; its transcript is `agent_session_events`."""

    __tablename__ = "agent_sessions"

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    agent_slug: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[int] = mapped_column(Integer)
    #: Who opened it: the only person who may talk in it, read it or close it.
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    #: `open`, `idle` (a conversation, inactive), `closed` (by its person or at its expiry) or `failed`.
    status: Mapped[str] = mapped_column(String(16), default="open")
    model: Mapped[str] = mapped_column(String(200), default="")
    gateway_key_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: The chat history the next response continues, the system message first.
    messages: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    responses: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Why it closed: `closed`, `expired`, `idle`, `agent_stopped`, `budget_exceeded`, `model_error`,
    #: `pod_failed`.
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: A hosted session's pod (S41-11), as its workflow last wrote it: `{state, kind, name,
    #: namespace}`, `state` one of `requested`, `running`, `stopped`. Null before the first response.
    pod: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: What a hosted session's runner reads at start (`GET /internal/agent-sessions/{id}/input`): no
    #: gateway key, no token — those reach the pod through its Secret only.
    stage_input: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: `session` (a key for the whole session, one hour: `/responses`) or `conversation` (a key per turn,
    #: closed on inactivity and resumed on demand; ADR 0134 §2-4).
    kind: Mapped[str] = mapped_column(String(16), default="session", server_default="session")
    #: The installation an assistant's conversation belongs to. No foreign key until the installations
    #: table (S63-02) and this one are both on the branch: the key is added then.
    installation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    #: Generated from the first exchange; null until then.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: What the surface that opened the conversation told the agent: a hint, never a resolved subject.
    context: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: The subjects a resolver tool attested in this conversation, indexed for erasure.
    resolved_subjects: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list, server_default="[]")
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When the daily purge may delete it (set at each turn from the installation's policy; S63-16).
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The revision of the agent the conversation started with, which it keeps.
    agent_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Turns taken: the `n` of the next turn's key, `turn:<conversation>:<n>`.
    turns: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: The conversation's own cap (`per_conversation` USD): its turns are reserved from what is left of it.
    max_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: Minutes of inactivity after which it is `idle`; null is the default (240).
    idle_close_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Turns a conversation may take in all; null is the default (200). The turn past it is refused (S63-04b).
    max_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Days a conversation is kept after its last turn: `retention_until` moves by it; null is 30 (S63-04b).
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The turns compression took out of `messages` (replaced there by a summary): what the person reads.
    archived_messages: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list, server_default="[]")
    #: Set while a turn runs: a second turn is refused until it ends or it lapses (S63-04b).
    turn_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set by a cancel that reached a replica other than the one running the turn; the turn polls it (S63-23).
    turn_cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentSessionEvent(Base, PkMixin):
    """One event of a session's transcript, in order: what the person said, each model turn, each
    tool call or refusal, how each response ended. Text only: audio is never journaled."""

    __tablename__ = "agent_session_events"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_agent_session_events_seq"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConversationFeedback(Base, PkMixin, TimestampMixin):
    """A thumb on one answer of a session, with an optional comment (S63-17, ADR 0140). The answer is the
    session's `response_seq`-th response, kept with the agent revision that gave it. A thumb down also
    holds a `candidate` eval case: pseudonymised, `proposed` to the owners, in no suite until one of them
    adds it."""

    __tablename__ = "conversation_feedback"
    __table_args__ = (UniqueConstraint("session_id", "response_seq", name="uq_conversation_feedback_answer"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"), index=True)
    #: The person who gave it: the session's opener.
    user_id: Mapped[str] = mapped_column(String(200), index=True)
    agent_slug: Mapped[str] = mapped_column(String(64), index=True)
    agent_version: Mapped[int] = mapped_column(Integer)
    #: 1-based position of the answer among the session's responses.
    response_seq: Mapped[int] = mapped_column(Integer)
    #: `up` or `down`.
    rating: Mapped[str] = mapped_column(String(8))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The candidate case of a thumb down (a `conversation-evals` case without assertions), pseudonymised.
    candidate: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    #: `proposed`, `accepted` or `dismissed` by an owner; null without a candidate.
    candidate_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
