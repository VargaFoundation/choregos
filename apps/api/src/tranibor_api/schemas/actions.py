# SPDX-License-Identifier: Apache-2.0
"""Governed actions (ADR 0035): what is proposed, what is decided, what happened."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field

from .base import Dto


class ActionEffectSpec(Dto):
    """An effect: its declared name (`connector.call`...), its parameters (rendered in isolated Jinja with
    `params` and `effects`), and what undoes it if a later step fails."""

    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)

    effect: str = Field(min_length=1, max_length=128)
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    compensate: dict[str, Any] | None = None


class Approver(Dto):
    role: Literal["developer", "release_captain", "project_owner", "org_admin"] = "project_owner"
    #: An identity-provider group the approver must also belong to (ADR 0052).
    group: str | None = Field(default=None, min_length=1, max_length=200)
    min: int = Field(default=1, ge=1, le=5)


class ApprovalSpec(Dto):
    approvers: list[Approver] = Field(default_factory=lambda: [Approver()], min_length=1)
    step_up_minutes: int = Field(default=10, ge=1, le=120)
    separation_of_duties: bool = True


class ActionCreate(Dto):
    kind: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    justification: str | None = Field(default=None, max_length=4000)
    params: dict[str, Any] = Field(default_factory=dict)
    effects: list[ActionEffectSpec] = Field(min_length=1, max_length=50)
    approval: ApprovalSpec = Field(default_factory=ApprovalSpec)
    work_item_id: str | None = None


class ActionDecisionBody(Dto):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=2000)


class ActionSettlementBody(Dto):
    """What a person found in the system an action in doubt wrote to (S65-26, ADR 0149 §5)."""

    model_config = ConfigDict(extra="forbid")

    outcome: Literal["made", "not_made"]
    note: str = Field(min_length=1, max_length=2000)
    #: With `made` only: what the write returned, as the person read it; the effects after it cite it.
    result: dict[str, Any] | None = None


class ActionSettlementDto(Dto):
    position: int
    outcome: str
    by: str
    at: datetime
    note: str
    result: dict[str, Any] | None = None
    auth_age_seconds: int | None = None
    #: What was sent and never answered, as the effect in doubt held it.
    in_doubt: list[dict[str, Any]] = Field(default_factory=list)


class ActionEffectDto(Dto):
    position: int
    key: str
    effect: str
    status: str
    attempts: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    finished_at: datetime | None = None
    settlement: ActionSettlementDto | None = None


class ActionDto(Dto):
    id: str
    origin: str
    kind: str
    title: str
    justification: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    effects: list[dict[str, Any]] = Field(default_factory=list)
    proposed_by: dict[str, Any] = Field(default_factory=dict)
    approval: dict[str, Any] = Field(default_factory=dict)
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    temporal_wf_id: str | None = None
    work_item_id: str | None = None
    run_id: str | None = None
    #: the project, for the organisation's decision inbox
    project_slug: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
    #: the effects log, key by key
    journal: list[ActionEffectDto] = Field(default_factory=list)
    #: what a person recorded once the action was in doubt (S65-26): the same as on its effect
    settlement: ActionSettlementDto | None = None
