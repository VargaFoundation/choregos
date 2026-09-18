"""Événements : entrants normalisés (InboundEvent), internes (CloudEvents), décisions humaines."""

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
    """Événement externe normalisé par un adaptateur (docs/plan/01 §1.7)."""

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
    """Événement interne au format CloudEvents 1.0."""

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
    """Décision humaine acheminée vers un WorkflowInterpreter."""

    request_id: str | None = None
    kind: HumanRequestKind
    approved: bool | None = None
    answer: str | None = None
    granted_paths: list[str] = Field(default_factory=list)
    reason: str | None = None
    decided_by: str
    decided_at: datetime = Field(default_factory=_now)
    channel: Literal["web", "tracker", "slack", "cli", "api", "board"] = "web"


class Control(Strict):
    """Signal de contrôle d'un workflow (pause, reprise, arrêt, migration)."""

    action: Literal["pause", "resume", "stop", "migrate", "rerun_stage"]
    workflow_def_id: str | None = None
    state_mapping: dict[str, str] = Field(default_factory=dict)
    run_id: str | None = None
    reason: str | None = None
    by: str | None = None


class DeployResult(Strict):
    """Résultat d'un passage dans le release train, renvoyé à l'interpréteur."""

    ok: bool
    env: str
    release_id: str | None = None
    revision: str | None = None
    rolled_back: bool = False
    detail: str | None = None
