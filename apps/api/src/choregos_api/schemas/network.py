# SPDX-License-Identifier: Apache-2.0
"""Network access: what a run may reach and what the network said about it (ADR 0070)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto


class RunNetwork(Dto):
    """The network ONE run may use: agent ∩ project ∩ organisation, plus the repository host."""

    run_id: str
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    implied: list[str] = Field(default_factory=list)
    per_host_per_minute: int | None = None
    per_run_per_minute: int | None = None
    org_per_host_per_minute: int | None = None
    builtin_web_tools: bool = True
    listed: bool = True
    org: str
    project: str
    agent: str | None = None


class NetworkDecisionIn(Dto):
    layer: Literal["proxy", "runner"]
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=0, ge=0, le=65535)
    decision: Literal["allowed", "denied", "rate_limited", "unreachable"]
    reason: str = Field(default="", max_length=200)
    #: Cumulative since the first sighting.
    count: int = Field(default=1, ge=1)
    bytes_up: int = Field(default=0, ge=0)
    bytes_down: int = Field(default=0, ge=0)
    first_at: datetime | None = None
    last_at: datetime | None = None


class NetworkDecisionBatch(Dto):
    decisions: list[NetworkDecisionIn] = Field(max_length=500)


class EffectiveNetworkDto(Dto):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    implied: list[str] = Field(default_factory=list)
    per_host_per_minute: int | None = None
    per_run_per_minute: int | None = None
    org_per_host_per_minute: int | None = None
    builtin_web_tools: bool = True


class ProjectNetwork(Dto):
    policy: dict[str, Any]
    ceiling: dict[str, Any] | None = None
    effective: EffectiveNetworkDto
    clamps: list[str] = Field(default_factory=list)


class NetworkActivityRow(Dto):
    host: str
    layer: str
    decision: str
    reason: str
    count: int
    runs: int
    projects: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    last_at: datetime


class NetworkActivity(Dto):
    rows: list[NetworkActivityRow] = Field(default_factory=list)
