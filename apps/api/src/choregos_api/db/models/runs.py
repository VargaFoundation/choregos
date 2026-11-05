# SPDX-License-Identifier: Apache-2.0
"""What a run leaves behind: actions and effects, hand-offs, artifacts, decisions, inbound events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin, utcnow


class A2aPushConfig(Base, PkMixin, TimestampMixin):
    """A URL an A2A client registered to hear of a task (ADR 0078 §5, S40-23d).

    Bound to the `chg_` token that registered it: deleted with it, and read again before each
    delivery. The signing secret and the client's own token are sealed in the secret store under
    `a2a/push/<id>`, never here. `cursor_*` is the last event delivered: a delivery that fails
    leaves it, and the next pass sends again (at least once)."""

    __tablename__ = "a2a_push_configs"
    __table_args__ = (
        UniqueConstraint("work_item_id", "client", "config_id", name="uq_a2a_push_configs_client_config"),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    api_token_id: Mapped[str] = mapped_column(ForeignKey("api_tokens.id", ondelete="CASCADE"), index=True)
    #: `jeton:<id>`, as the door's rate limit and audit name the client.
    client: Mapped[str] = mapped_column(String(255))
    #: The client's id for this registration (A2A `pushNotificationConfig.id`).
    config_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    cursor_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cursor_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: What the last delivery said (A2A state, workflow state, pending request): an event that
    #: changes none of it moves the cursor without a call.
    last_view: Mapped[str | None] = mapped_column(String(400), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(400), nullable=True)
    #: Set once the final state is delivered, or the registration given up: no more deliveries.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebhookDelivery(Base, PkMixin):
    """Webhook deduplication: a delivery is processed only once."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("source", "delivery_id", name="source_delivery"),)

    source: Mapped[str] = mapped_column(String(32), index=True)
    delivery_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ReceivedEvent(Base, PkMixin):
    """A webhook event a project received, and what the platform did with it (S42-06f).

    One row per event of a verified delivery that reached a project — never the payload, a header, a
    comment or an email: the summary is built from typed fields. `webhook_deliveries` stays the
    deduplication table (one row per delivery, written before the outcome is known)."""

    __tablename__ = "received_events"
    __table_args__ = (Index("ix_received_events_project_received", "project_id", "received_at"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True
    )
    work_item_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: `jira`, `github`, `gitlab`, `tekton`, `argocd`, `alertmanager`, `plugin:<plugin>/<server>`,
    #: `trigger:<name>` (S56-01), `schedule:<name>`
    source: Mapped[str] = mapped_column(String(160))
    event_type: Mapped[str] = mapped_column(String(80))
    delivery_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: `created_work_item`, `started`, `signalled`, `boarded`, `labels_updated`,
    #: `ignored_no_item_key`, `ignored_not_agent_ready`, `already_open` or `refused` (S56-01)
    outcome: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(String(300), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TriggerToken(Base, PkMixin):
    """The token of a project's HTTP trigger (S56-01, ADR 0113): its SHA-256, never the token.

    The trigger itself is declared in the project's configuration (`triggers:`); this row is what a
    caller must prove. Minting a new one replaces the old: one token per trigger, shown once."""

    __tablename__ = "trigger_tokens"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_trigger_tokens_project_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(63))
    token_sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DecisionRecordRow(Base, PkMixin):
    """One answered question and what followed it: the decision ledger (ADR 0121 §6, S52-05).

    A decision step's answer (`source = decision_model`), the confidence an agent declared
    (`source = agent`), one row per run and question, or a work item's routing by a decision model
    (`source = router`, no run, one row per work item: ADR 0123), written once.
    `transition` is filled when the workflow selects its next transition; the outcome fields when they
    become known — a person's verdict (`overridden`), the work item's end (`terminal`), its return
    (`reopened`). Calibration is computed from these rows (`services.decision_ledger`)."""

    __tablename__ = "decision_records"
    __table_args__ = (
        UniqueConstraint("run_id", "question", name="uq_decision_records_run_question"),
        Index("ix_decision_records_step", "project_id", "workflow", "step"),
        # A routing's record has no run (ADR 0123): one per work item and question.
        Index(
            "uq_decision_records_work_item_question_without_run",
            "work_item_id",
            "question",
            unique=True,
            postgresql_where=text("run_id IS NULL"),
            sqlite_where=text("run_id IS NULL"),
        ),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    #: `None` for a work item's routing, recorded at its birth before any run (ADR 0123).
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True, nullable=True
    )
    #: The work item's tracker key and the project's slug, as the contract names them.
    work_item_key: Mapped[str] = mapped_column(String(255))
    project_slug: Mapped[str] = mapped_column(String(64))
    workflow: Mapped[str] = mapped_column(String(100))
    workflow_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The state the step ran from, and the step's transition.
    step: Mapped[str] = mapped_column(String(64))
    transition_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question: Mapped[str] = mapped_column(String(64))
    #: `decision_model` or `agent`.
    source: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(200))
    model_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: A boolean, a number or a short text, kept as JSON.
    answer: Mapped[Any] = mapped_column(Json)
    probability: Mapped[float] = mapped_column(Float)
    #: The state the workflow went to after the step, once known.
    transition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    overridden: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: `success`, `declined` or `cancelled`.
    terminal: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reopened: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Action(Base, PkMixin, TimestampMixin):
    """A governed action (ADR 0035): proposed, decided by re-authenticated humans,
    executed by Temporal (`action-<id>`), never inside the request that approves it. Its effects
    are recorded one by one (`action_effects`): a resumption does not redo what is done."""

    __tablename__ = "actions"
    __table_args__ = (Index("ix_actions_project_status", "project_id", "status"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str] = mapped_column(String(16))
    #: what it does, as a name (`entra.create_user`, `arrivee.comptes`)  # lang: fr-ok - a recorded kind
    kind: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(300))
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: [{effect, with, compensate?}]: played in order, compensated in reverse
    effects: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    #: {kind: user|agent|system, id, via?}
    proposed_by: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: {approvers: [{role, min}], step_up_minutes, separation_of_duties}
    approval: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    status: Mapped[str] = mapped_column(String(24), default="pending_approval")
    result: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    temporal_wf_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionEffect(Base, PkMixin, TimestampMixin):
    """An effect of an action, under its KEY (`<action>:<n>`): recorded BEFORE it is attempted,
    confirmed after. A replayed activity sees the key done and redoes nothing; an effect started
    and not confirmed (a killed worker) is retried: effects are idempotent by construction."""

    __tablename__ = "action_effects"
    __table_args__ = (UniqueConstraint("action_id", "position", name="uq_action_effects_action_position"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(128), unique=True)
    effect: Mapped[str] = mapped_column(String(128))
    params: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: started | done | compensated | compensation_failed
    status: Mapped[str] = mapped_column(String(24), default="started")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HandOffReceipt(Base, PkMixin, TimestampMixin):
    """A receipt for a hand-off between stages (S25-04): a stage output, its digest, and the run that
    PRODUCED or READ it. A row never changes: a new revision makes a new receipt, and so does
    a read. This is what shows that the next stage read the exact revision produced,
    and that an output modified afterwards no longer has the digest of its receipt."""

    __tablename__ = "hand_off_receipts"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id", "output", "digest", "run_id", "kind", name="uq_hand_off_receipts_passage"
        ),
    )

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    #: the name of the output: `spec_markdown`, `plan_markdown`, or the one a transition declares
    output: Mapped[str] = mapped_column(String(128))
    #: `sha256:<hex>` of the value, in canonical JSON (`services/recus.py`)
    digest: Mapped[str] = mapped_column(String(80))
    #: produced | read
    kind: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[str] = mapped_column(String(64))
    #: the role of the run's stage, kept for display: a deleted run leaves its receipt readable
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ArtifactRecord(Base, PkMixin, TimestampMixin):
    """One revision of a named output of a work item (ADR 0049): typed, stored, traced.

    An inline artifact (markdown, JSON) keeps its value in `content`; a file keeps its bytes in the
    object store under `storage_key`. Either way `digest` is the sha256 of the bytes served on
    download, recomputed on every read. A row never changes: a new content makes the next version.
    """

    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("work_item_id", "name", "version", name="uq_artifacts_version"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer)
    #: a key of `choregos_contracts.ARTIFACT_TYPES`
    type: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    media_type: Mapped[str] = mapped_column(String(200))
    digest: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    content: Mapped[Any | None] = mapped_column(Json, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    storage_url: Mapped[str | None] = mapped_column(String(700), nullable=True)
    #: who produced it: the run, its stage and attempt, the registry agent and its version
    run_id: Mapped[str] = mapped_column(String(128))
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
