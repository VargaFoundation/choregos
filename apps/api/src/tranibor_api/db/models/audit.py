# SPDX-License-Identifier: Apache-2.0
"""The audit log and network decisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class AuditLog(Base, PkMixin):
    """The audit log. `org_id` is NULL for a platform event (a sign-in, a
    token): those rows are visible only to a session with scope `*`. Without this column,
    the table was outside RLS and the route filtered nothing: a `project_owner` of one
    organisation read the audit of all the others.

    On PostgreSQL it is a hash chain per organisation and append-only (ADR 0182): `seq`, `prev_hash` and
    `hash` are written by the database's own trigger at the insert, whatever the writer put there, and
    stay NULL on SQLite. Nothing in the application reads them; `tranibor_api.audit_chain` verifies them."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_chain", "org_id", "seq"),)

    actor_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    actor_kind: Mapped[str] = mapped_column(String(16), default="user")
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: The entry's place in its chain (1, 2, ...), the hash of the entry before it, and its own hash.
    seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuditChainHead(Base):
    """The last link of one audit chain (ADR 0182): an organisation's, or the platform's (`chain = ''`).

    Written only by the trigger that links an entry, whose lock on this row puts the writers of one chain
    in a line. A cache of the chain's end, not evidence: the verifier walks the entries, and compares their
    end with this row to see entries deleted from the end."""

    __tablename__ = "audit_chain_heads"

    #: The organisation's id, or `''` for the platform's entries.
    chain: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    seq: Mapped[int] = mapped_column(BigInteger)
    hash: Mapped[str] = mapped_column(String(64))


class AuditCheckpoint(Base, PkMixin):
    """A point of a chain someone vouched for (ADR 0182), append-only like the log.

    `genesis`: what the migration chained of the history that existed, unsigned; the chain proves nothing
    of those entries before that moment. `signed`: a head `choregos-admin audit checkpoint` verified and
    signed with an Ed25519 key the deployment keeps outside the database (`key_id` is its public half, as
    the outposts write a key; `signature` the unpadded base64url of 64 bytes)."""

    __tablename__ = "audit_checkpoints"

    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    seq: Mapped[int] = mapped_column(BigInteger)
    hash: Mapped[str] = mapped_column(String(64))
    #: `genesis` or `signed`
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    key_id: Mapped[str | None] = mapped_column(String(43), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(86), nullable=True)


class AuditExportCursor(Base):
    """How far one SIEM sink has been given one audit chain (ADR 0196): the chain's `seq` and `hash` of the
    last entry the sink accepted, so a restart resumes there.

    Moved only after the sink accepted a batch, in the transaction that holds this row's lock: at-least-once,
    never ahead of the sink. Not evidence (the chain is): losing the row re-ships the chain from its first
    entry. `chain` is the organisation's id, or `''` for the platform's chain, as `audit_chain_heads`."""

    __tablename__ = "audit_export_cursors"

    #: The sink's name (`TRANIBOR_AUDIT_EXPORT_NAME`): another name is another cursor, shipped from entry 1.
    sink: Mapped[str] = mapped_column(String(64), primary_key=True)
    chain: Mapped[str] = mapped_column(String(36), primary_key=True)
    org_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, default=0)
    hash: Mapped[str] = mapped_column(String(64), default="0" * 64)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NetworkDecision(Base, PkMixin, TimestampMixin):
    """What reached or was refused at the network, aggregated per run (ADR 0070).

    One row per (run, layer, host, port, decision, reason). The counters are CUMULATIVE totals sent
    by the reporter (the egress proxy, the runner): a replayed batch replaces them, it never adds."""

    __tablename__ = "network_decisions"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "layer", "host", "port", "decision", "reason", name="uq_network_decisions_key"
        ),
        Index("ix_network_decisions_org_last", "org_id", "last_at"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: `proxy`, `runner`, `broker` or `platform`
    layer: Mapped[str] = mapped_column(String(16))
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer, default=0)
    #: `allowed`, `denied`, `rate_limited` or `unreachable`
    decision: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(String(200), default="")
    count: Mapped[int] = mapped_column(Integer, default=1)
    bytes_up: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_down: Mapped[int] = mapped_column(BigInteger, default=0)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
