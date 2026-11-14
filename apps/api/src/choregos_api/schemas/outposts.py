# SPDX-License-Identifier: Apache-2.0
"""Outposts as their organisation manages them (ADR 0147): the bodies of `/orgs/{org}/outposts…`.

Public data only. Nothing of an outpost's side exists here, because the platform stores none of it:
no address, no credential, no private key (ADR 0147 §7). An enrolment token is the one secret, and
only the answer that issues it carries it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from choregos_contracts import (
    OutpostAnswerOutcome,
    OutpostCallKind,
    OutpostCallState,
    OutpostCapability,
    OutpostInstanceStatus,
    OutpostRefusalCode,
    OutpostStatus,
)
from choregos_contracts.outpost import (
    ENROLMENT_TTL_S,
    FINGERPRINT_PATTERN,
    OUTPOST_SLUG_PATTERN,
)
from pydantic import Field, field_validator

from .base import Dto, PageMeta

ENROLMENT_MAX_MINUTES = ENROLMENT_TTL_S // 60


def _tools() -> list[Literal["tools"]]:
    return ["tools"]


class OutpostCreate(Dto):
    #: The name connectors route by and the journal shows (`outpost:<slug>/<alias>`).
    slug: Annotated[str, Field(pattern=OUTPOST_SLUG_PATTERN)]
    display_name: Annotated[str, Field(max_length=200)] | None = None
    #: What the organisation enables it for; `tools` is the one capability that can be enabled.
    capabilities: Annotated[list[Literal["tools"]], Field(min_length=1)] = Field(default_factory=_tools)

    @field_validator("capabilities")
    @classmethod
    def _a_capability_is_enabled_once(cls, capabilities: list[str]) -> list[str]:
        if len(set(capabilities)) != len(capabilities):
            raise ValueError("a capability is listed once")
        return capabilities


class OutpostInstanceCounts(Dto):
    pending: int = 0
    active: int = 0
    revoked: int = 0


class OutpostDto(Dto):
    """An outpost as its organisation keeps it."""

    slug: str
    display_name: str
    status: OutpostStatus
    capabilities: list[OutpostCapability]
    #: Moved by every revocation, of the outpost or of one instance (ADR 0147 §5).
    epoch: int
    #: An active instance polled within the poll window.
    online: bool
    last_seen_at: datetime | None
    instances: OutpostInstanceCounts
    #: The endpoint aliases its active instances announce: names only.
    endpoints: list[str]
    #: The `mcp-outpost` connectors that route to it; it is not deleted while one remains.
    connectors: list[str]
    #: The door's origin, as the outpost's policy file names it (`control_plane`): the deployment's, the
    #: same for every outpost; null when it declares none.
    control_plane: str | None
    #: The door's public keys an operator pins in the outpost's policy file (ADR 0148 §13): the one
    #: that signs now first. Empty while the deployment's door has no signing key.
    door_keys: list[str]
    created_by: str | None
    created_at: datetime


class OutpostList(Dto):
    items: list[OutpostDto]
    #: The deployment keeps outposts switched on (`CHOREGOS_OUTPOSTS`): off, a creation and an
    #: enrolment token are refused (409 `outposts_disabled`).
    enabled: bool
    #: The door has a signing key: without one, an enrolment token is refused (409 `door_key_missing`).
    door_signing_key: bool


class OutpostEnrolmentCreate(Dto):
    expires_in_minutes: Annotated[int, Field(ge=1, le=ENROLMENT_MAX_MINUTES)] = ENROLMENT_MAX_MINUTES


class OutpostEnrolmentCreated(Dto):
    """An enrolment token, shown once. The platform keeps its digest."""

    id: str
    #: The plaintext token: what `choregos-outpost enrol` presents.
    token: str = Field(repr=False)
    expires_at: datetime
    #: The door's origin, as the outpost's policy file names it; null when the deployment declares none.
    control_plane: str | None
    #: The door's public keys to pin in the outpost's policy file before the daemon starts.
    door_keys: Annotated[list[str], Field(min_length=1)]


class OutpostEnrolmentDto(Dto):
    """An enrolment token that can still enrol an instance: never the token, whose digest is all that
    is kept."""

    id: str
    expires_at: datetime
    #: Who issued it.
    created_by: str | None
    created_at: datetime


class OutpostEnrolmentList(Dto):
    items: list[OutpostEnrolmentDto]


class OutpostInstanceDto(Dto):
    """One copy of the daemon. Only public data."""

    id: str
    status: OutpostInstanceStatus
    fingerprint: str
    daemon_version: str
    protocol: int
    capabilities: list[OutpostCapability]
    endpoints: list[str]
    facts: dict[str, str]
    #: The enrolment token it enrolled with, the last one when a pending key enrolled again.
    enrolment_id: str | None
    #: Who issued that token.
    enrolled_by: str | None
    enrolled_at: datetime
    #: How recent the sign-in of the person who approves it must be, in minutes (the deployment's).
    approval_step_up_minutes: int
    approved_by: str | None
    approved_at: datetime | None
    revoked_by: str | None
    revoked_at: datetime | None
    last_seen_at: datetime | None
    #: Active, and it polled within the poll window.
    online: bool
    #: The calls it holds and has not answered.
    in_flight: int


class OutpostInstanceList(Dto):
    items: list[OutpostInstanceDto]


class OutpostInstanceApprove(Dto):
    #: The fingerprint the daemon printed, as the person read it there.
    fingerprint: Annotated[str, Field(pattern=FINGERPRINT_PATTERN)]


class OutpostCallRecord(Dto):
    """What the platform keeps of a call through an outpost: references and digests, never its
    arguments or its result (ADR 0148 §11)."""

    id: str
    kind: OutpostCallKind
    state: OutpostCallState
    connector: str | None
    endpoint: str
    tool: str | None
    write: bool
    instance_id: str | None
    epoch: int | None
    outcome: OutpostAnswerOutcome | None
    is_error: bool | None
    refusal: OutpostRefusalCode | None
    arguments_sha256: str | None
    result_sha256: str | None
    project: str | None
    run_id: str | None
    action_id: str | None
    created_at: datetime
    claimed_at: datetime | None
    finished_at: datetime | None


class OutpostCallPage(Dto):
    items: list[OutpostCallRecord]
    meta: PageMeta
