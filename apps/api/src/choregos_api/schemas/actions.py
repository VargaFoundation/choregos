# SPDX-License-Identifier: Apache-2.0
"""Les actions gouvernées (ADR 0035) : ce qu'on propose, ce qu'on décide, ce qui s'est passé."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import ConfigDict, Field

from .base import Dto


class ActionEffectSpec(Dto):
    """Un effet : son nom déclaré (`connector.call`…), ses paramètres (rendus en Jinja isolé avec
    `params` et `effects`), et ce qui le défait si une suite échoue."""

    model_config = ConfigDict(extra="forbid", from_attributes=True, populate_by_name=True)

    effect: str = Field(min_length=1, max_length=128)
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    compensate: dict[str, Any] | None = None


class Approbateur(Dto):
    role: Literal["developer", "release_captain", "project_owner", "org_admin"] = "project_owner"
    min: int = Field(default=1, ge=1, le=5)


class ApprovalSpec(Dto):
    approvers: list[Approbateur] = Field(default_factory=lambda: [Approbateur()], min_length=1)
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


class ActionEffectDto(Dto):
    position: int
    key: str
    effect: str
    status: str
    attempts: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    finished_at: datetime | None = None


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
    #: le projet, pour la boîte des décisions de l'organisation
    project_slug: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
    #: le journal des effets, clé par clé
    journal: list[ActionEffectDto] = Field(default_factory=list)
