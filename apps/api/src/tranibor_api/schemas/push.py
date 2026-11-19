# SPDX-License-Identifier: Apache-2.0
"""The DTOs of Web Push (ADR 0194): a person's devices and the key their phone subscribes with."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import Dto

PushKind = Literal["decision.awaiting", "run.ended", "agent.notification", "session.ended"]


class PushVapidKeyDto(Dto):
    public_key: str


class PushKeysDto(Dto):
    p256dh: str = Field(min_length=1, max_length=128)
    auth: str = Field(min_length=1, max_length=64)


class PushSubscriptionCreateDto(Dto):
    endpoint: str = Field(min_length=1, max_length=2048)
    keys: PushKeysDto
    device_name: str = Field(min_length=1, max_length=80)
    kinds: list[PushKind] | None = None


class PushSubscriptionDto(Dto):
    id: str
    device_name: str
    #: Where the device is registered; the endpoint itself is a capability and is never returned.
    endpoint_host: str
    kinds: list[PushKind] | None = None
    created_at: datetime
    last_delivered_at: datetime | None = None
    last_failure_at: datetime | None = None


class PushTestResultDto(Dto):
    delivered: bool
    status_code: int | None = None
    removed: bool
