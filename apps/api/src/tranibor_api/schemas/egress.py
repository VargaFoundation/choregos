# SPDX-License-Identifier: Apache-2.0
"""The organisation's egress allowlist, its requests and its checks (S39-22, ADR 0066)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from .base import Dto

Family = Literal["api", "workers", "runners"]
RuleStatus = Literal["requested", "allowed", "refused", "revoked"]


class EgressRule(Dto):
    id: str
    host: str
    ports: list[int]
    used_by: list[Family]
    status: RuleStatus
    reason: str = ""
    #: `mcp-catalogue:<id>@<version>`, `org-connector:<name>`, `skill-import:<repo>`, `manual`, `migration`
    origin: str = "manual"
    requested_by: str | None = None
    requested_at: datetime | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None


class EgressTarget(Dto):
    """A host to check: a URL (its host and port are read), or a host and port."""

    url: str | None = Field(default=None, max_length=2000)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    used_by: list[Family] = Field(default_factory=lambda: list[Family](["api"]), min_length=1)


class EgressOrigin(Dto):
    """What the server derives the hosts from, so the console never asks a person for one."""

    kind: Literal["org-connector", "skill-import", "knowledge-source", "mcp-catalogue", "remote-agent"]
    #: `org-connector`: `type` and `config`; `skill-import`: `repo_url`; `knowledge-source`: `kind`
    #: and `config`
    type: str | None = None
    config: dict[str, object] = Field(default_factory=dict)
    repo_url: str | None = None
    name: str | None = None


class Approver(Dto):
    display_name: str
    email: str


class PlatformChange(Dto):
    #: whether the platform's network may need a change for this host
    needed: bool
    format: Literal["cilium-fqdn", "kubernetes-networkpolicy", "text"] = "text"
    snippet: str | None = None
    issue_url: str | None = None
    owner: str | None = None


class EgressVerdict(Dto):
    host: str
    port: int
    used_by: Family
    #: this organisation's list: `allowed`, `requested` (waiting for a decision) or `absent`
    org: Literal["allowed", "requested", "absent"]
    #: the platform's network as the deployment declares it: `open`, `outside` or `unknown`
    ceiling: Literal["open", "outside", "unknown"]
    can_allow: bool
    approvers: list[Approver] = Field(default_factory=list)
    platform_change: PlatformChange
    requested_by: str | None = None
    requested_at: datetime | None = None


class EgressCheck(Dto):
    targets: Annotated[list[EgressTarget], Field(max_length=50)] = Field(default_factory=list)
    origin: EgressOrigin | None = None
    reason: str = Field(default="", max_length=500)


class EgressCheckResult(Dto):
    verdicts: list[EgressVerdict]
    #: every verdict is `allowed` and not outside the ceiling
    ok: bool


class EgressRequired(Dto):
    """The 409 body of every governed "add" while a host is missing."""

    code: Literal["egress_required"] = "egress_required"
    verdicts: list[EgressVerdict]


class EgressRuleCreate(Dto):
    host: str = Field(min_length=1, max_length=255)
    ports: list[int] = Field(default_factory=lambda: [443], min_length=1, max_length=10)
    used_by: list[Family] = Field(default_factory=lambda: list[Family](["api"]), min_length=1)
    reason: str = Field(default="", max_length=500)
    origin: str = Field(default="manual", max_length=200)


class EgressRequestCreate(EgressRuleCreate):
    pass


class EgressDecision(Dto):
    note: str | None = Field(default=None, max_length=500)


class EgressOverview(Dto):
    mode: Literal["enforce", "warn", "off"]
    #: family -> host patterns the deployment declares open; an absent family is unknown
    ceiling: dict[str, list[str]]
    rules: list[EgressRule]
    requests: list[EgressRule]
    #: whether the caller may decide (allow, refuse, revoke)
    can_decide: bool = False
