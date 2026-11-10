# SPDX-License-Identifier: Apache-2.0
"""Assistant installations (ADR 0133): the DTOs of `/orgs/{org}/assistants`, as the contract declares them."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .base import Dto

SLUG = r"^[a-z0-9][a-z0-9-]{0,62}$"
NEED = r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$"
Channel = Literal["stable", "candidate"]
Status = Literal["incomplete", "ready", "suspended"]


class AssistantInstall(Dto):
    plugin: str = Field(pattern=SLUG)
    assistant: str = Field(pattern=SLUG)
    slug: str | None = Field(default=None, pattern=SLUG)
    config: dict[str, Any] = Field(default_factory=dict)
    owner_groups: list[str] = Field(default_factory=list)
    audience_groups: list[str] = Field(default_factory=list)
    channel: Channel = "stable"


class AssistantUpdate(Dto):
    config: dict[str, Any] | None = None
    owner_groups: list[str] | None = None
    audience_groups: list[str] | None = None
    channel: Channel | None = None
    policy_overrides: dict[str, Any] | None = None


class AssistantBind(Dto):
    connector: str = Field(min_length=1, max_length=64)
    enabled: bool = True


class AssistantBinding(Dto):
    need: str
    connector: str
    operations: list[str]
    identity: Literal["service", "user_exchange", "user_consent"]
    approval: Literal["none", "inline", "console"] | None = None
    enabled: bool


class Assistant(Dto):
    slug: str
    plugin: str
    plugin_version: str
    assistant_name: str
    display_name: str | None = None
    config: dict[str, Any]
    owner_groups: list[str]
    audience_groups: list[str]
    channel: Channel
    status: Status
    policy_overrides: dict[str, Any]
    bindings: list[AssistantBinding]
    created_by: str | None = None
    created_at: datetime


class AssistantList(Dto):
    items: list[Assistant]


class AssistantReadinessItem(Dto):
    kind: Literal[
        "assistant_missing", "config_missing", "need_unbound", "need_not_served", "identity_unsupported"
    ]
    need: str | None = None
    key: str | None = None
    message: str
    fix_by: Literal["owners", "org_admin"]
    groups: list[str]


class AssistantReadiness(Dto):
    status: Status
    items: list[AssistantReadinessItem]


class MyAssistant(Dto):
    """An assistant the caller may talk to: the installations whose audience holds them (S63-15)."""

    org: str
    slug: str
    name: str
    display_name: str | None = None
    description: str | None = None
    status: Status


class MyAssistantList(Dto):
    items: list[MyAssistant]


class AssistantConversationOpen(Dto):
    """What opens an assistant's conversation: an optional context, validated against the manifest's
    `surfaces.embed.context_schema` and given to the agent as data, never as a resolved subject."""

    context: dict[str, Any] | None = None
