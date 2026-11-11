# SPDX-License-Identifier: Apache-2.0
"""What an assistant's manifest tells the console (S63-18): the form to install it, the pages of its user."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import Dto


class AssistantMemorySpace(Dto):
    """A memory space the assistant declares: the console's memory page lists them."""

    name: str
    scope: Literal["user", "user_subject", "installation"]
    kind: Literal["facts", "profile"]


class AssistantNeed(Dto):
    """A tool need the assistant declares, so the user sees which sources it can use."""

    need: str
    identity: Literal["service", "user_exchange", "user_consent"]
    access: str
    required: bool


class AssistantTaskDecl(Dto):
    """A task the assistant declares: the console subscribes the person to it."""

    name: str
    delegation: Literal["none", "read_only"]
    schedulable: bool


class PluginAssistant(Dto):
    """One assistant an installed plugin ships, as an owner installs it."""

    name: str
    display_name: str | None = None
    description: str | None = None
    config_schema: dict[str, Any] | None = None
    suggested_groups: list[str]
    needs: list[AssistantNeed]
    memory_spaces: list[AssistantMemorySpace] = Field(default_factory=list)


class PluginAssistantList(Dto):
    items: list[PluginAssistant]
