# SPDX-License-Identifier: Apache-2.0
"""A member's profile page (S66-42, ADR 0162): one read, shaped by who reads it."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from choregos_contracts import Role

from .base import Dto
from .platform import AuditEntry

#: `basic`: the card any member of the organisation reads; `full`: an administrator, or the person themself.
ProfileView = Literal["basic", "full"]
MemberStatus = Literal["active", "former"]
#: How the account came: through an identity provider's sign-in, pushed by one (SCIM), or invited by hand.
AccountOrigin = Literal["identity_provider", "provisioned", "manual"]
MemberGroupSource = Literal["internal", "scim", "identity_provider"]


class MemberProjectRole(Dto):
    project_slug: str
    project_name: str
    role: Role


class MemberGroup(Dto):
    slug: str
    name: str
    source: MemberGroupSource


class MemberSessions(Dto):
    open: int
    last_activity_at: datetime | None = None


class MemberPendingRequest(Dto):
    id: str
    work_item_id: str
    work_item_key: str
    work_item_title: str
    project_slug: str
    kind: str
    requested_at: datetime
    due_at: datetime | None = None
    #: `person`: addressed to them; `group`: to a group they are in.
    via: Literal["person", "group"]
    group: str | None = None


class MemberDecision(Dto):
    request_id: str
    work_item_id: str
    work_item_key: str
    work_item_title: str
    project_slug: str
    kind: str
    approved: bool | None = None
    decided_at: datetime


class MemberRun(Dto):
    id: str
    work_item_id: str
    work_item_key: str
    project_slug: str
    stage_role: str
    status: str
    started_at: datetime | None = None
    cost_usd: float


class MemberWork(Dto):
    pending_requests: list[MemberPendingRequest]
    decisions: list[MemberDecision]
    runs: list[MemberRun]
    spend_month_usd: float | None = None


class MemberAdministration(Dto):
    api_tokens: int
    audit: list[AuditEntry]


class MemberProfileDto(Dto):
    user_id: str
    org: str
    view: ProfileView
    status: MemberStatus
    display_name: str
    given_name: str | None = None
    family_name: str | None = None
    job_title: str | None = None
    department: str | None = None
    email: str
    avatar_url: str | None = None
    org_role: Role | None = None
    project_roles: list[MemberProjectRole]
    groups: list[MemberGroup]
    origin: AccountOrigin | None = None
    last_login_at: datetime | None = None
    sessions: MemberSessions | None = None
    work: MemberWork | None = None
    administration: MemberAdministration | None = None


class AvatarDto(Dto):
    avatar_url: str
