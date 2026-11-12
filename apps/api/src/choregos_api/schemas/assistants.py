# SPDX-License-Identifier: Apache-2.0
"""Assistant installations (ADR 0133): the DTOs of `/orgs/{org}/assistants`, as the contract declares them."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from .assistant_catalogue import AssistantMemorySpace, AssistantNeed, AssistantTaskDecl
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
    #: A write's approval, raised above the manifest's (`inline` to `console`), never below it (S63-10).
    approval: Literal["inline", "console"] | None = None


class AssistantBinding(Dto):
    need: str
    connector: str
    operations: list[str]
    identity: Literal["service", "user_exchange", "user_consent"]
    approval: Literal["none", "inline", "console"] | None = None
    enabled: bool


class AssistantStop(Dto):
    """Who stopped the assistant, when and why (ADR 0142 §4 bis)."""

    at: datetime
    by: str
    reason: str


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
    #: Set while a person has stopped the assistant: `status` then reads `suspended`.
    stopped: AssistantStop | None = None
    policy_overrides: dict[str, Any]
    bindings: list[AssistantBinding]
    created_by: str | None = None
    created_at: datetime


class AssistantNotStopped(Dto):
    kind: Literal["turn"] = "turn"
    id: str
    detail: str


class AssistantStopResult(Dto):
    """What stopping an assistant did (`POST /orgs/{org}/assistants/{slug}/stop`, ADR 0142 §4 bis)."""

    slug: str
    status: Literal["suspended"] = "suspended"
    #: Turns in flight whose cancel was requested: each settles at its next model or tool call.
    turns_cancelled: int
    not_stopped: list[AssistantNotStopped] = Field(default_factory=list)


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
    memory_spaces: list[AssistantMemorySpace] = Field(default_factory=list)
    tasks: list[AssistantTaskDecl] = Field(default_factory=list)
    needs: list[AssistantNeed] = Field(default_factory=list)


class MyAssistantList(Dto):
    items: list[MyAssistant]


class AssistantConversationOpen(Dto):
    """What opens an assistant's conversation: an optional context, validated against the manifest's
    `surfaces.embed.context_schema` and given to the agent as data, never as a resolved subject."""

    context: dict[str, Any] | None = None


class AssistantEmbed(Dto):
    """The origins that may frame the installation's embedded component (`config.embed_origins`)."""

    embed_origins: list[str]


class EmbedToken(Dto):
    """The frame token of the embedded component (S63-12): short-lived, one person, one installation."""

    token: str
    expires_in: int
