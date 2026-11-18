# SPDX-License-Identifier: Apache-2.0
"""Events: normalised inbound (InboundEvent), internal (CloudEvents), human decisions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from .enums import EventType, HumanRequestKind, InboundEventType
from .workflow import Strict


def _now() -> datetime:
    return datetime.now(UTC)


class InboundEvent(Strict):
    """External event normalised by an adapter (docs/plan/01 §1.7)."""

    type: InboundEventType
    source: str
    delivery_id: str
    ts: datetime = Field(default_factory=_now)
    project_slug: str | None = None
    work_item_key: str | None = None
    actor: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return f"{self.source}:{self.delivery_id}:{self.type}"


class ChoregosEvent(Strict):
    """Internal event in the CloudEvents 1.0 format."""

    specversion: Literal["1.0"] = "1.0"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str
    type: EventType
    subject: str | None = None
    time: datetime = Field(default_factory=_now)
    datacontenttype: str = "application/json"
    project_slug: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def emit(
        cls,
        type_: EventType,
        *,
        source: str = "/choregos/orchestrator",
        subject: str | None = None,
        project_slug: str | None = None,
        **data: Any,
    ) -> ChoregosEvent:
        return cls(type=type_, source=source, subject=subject, project_slug=project_slug, data=data)


class HumanDecision(Strict):
    """Human decision routed to a WorkflowInterpreter."""

    request_id: str | None = None
    kind: HumanRequestKind
    approved: bool | None = None
    answer: str | None = None
    granted_paths: list[str] = Field(default_factory=list)
    reason: str | None = None
    #: A task: the values entered (they become ticket fields) and the sentence the person
    #: attested, as it was shown to them (S20-06).
    values: dict[str, Any] = Field(default_factory=dict)
    attestation: str | None = None
    decided_by: str
    decided_at: datetime = Field(default_factory=_now)
    channel: Literal["web", "tracker", "slack", "cli", "api", "board"] = "web"


class Control(Strict):
    """Control signal for a workflow (pause, resume, stop, migrate)."""

    action: Literal["pause", "resume", "stop", "migrate", "rerun_stage"]
    workflow_def_id: str | None = None
    #: `migrate`: the target definition, read and checked by the API. The interpreter does no
    #: I/O: an identifier alone told it nothing, and it died while validating it (ADR 0031).
    workflow: dict[str, Any] | None = None
    state_mapping: dict[str, str] = Field(default_factory=dict)
    run_id: str | None = None
    reason: str | None = None
    by: str | None = None


class DeployResult(Strict):
    """Result of a pass through the release train, returned to the interpreter."""

    ok: bool
    env: str
    release_id: str | None = None
    revision: str | None = None
    rolled_back: bool = False
    detail: str | None = None
