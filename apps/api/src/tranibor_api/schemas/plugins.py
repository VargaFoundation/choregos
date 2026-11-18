# SPDX-License-Identifier: Apache-2.0
"""Installed plugins (ADR 0093): the DTOs of `/orgs/{org}/plugins`, as the contract declares them."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from .base import Dto

PLUGIN_NAME = r"^[a-z0-9][a-z0-9-]{0,62}$"


class PluginContents(Dto):
    agents: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    templates: list[str] = Field(default_factory=list)
    evals: list[str] = Field(default_factory=list)
    procedures: list[str] = Field(default_factory=list)
    servers: list[str] = Field(default_factory=list)


class InstalledPlugin(Dto):
    name: str
    version: str
    source: str
    digest: str
    publisher: str
    license: str
    description: str
    default: bool
    contents: PluginContents
    installed_by: str | None
    installed_at: datetime
    latest: str | None


class PluginList(Dto):
    items: list[InstalledPlugin]


class PluginInstall(Dto):
    source: str = Field(pattern=PLUGIN_NAME)
    name: str = Field(pattern=PLUGIN_NAME)
    version: str = Field(min_length=1, max_length=128)


class PluginUpgrade(Dto):
    version: str | None = Field(default=None, min_length=1, max_length=128)


class PluginUpgradeResult(Dto):
    plugin: InstalledPlugin
    previous_version: str
    offered: list[str]


class PluginInstallPending(Dto):
    """The 202 of an install or upgrade that waits for a second administrator (ADR 0050)."""

    status: Literal["pending_approval"] = "pending_approval"
    approval_id: str
    reason: str


class PluginApprovalDto(Dto):
    id: str
    action: Literal["install", "upgrade"]
    source: str
    name: str
    version: str
    digest: str
    reason: str
    default: bool
    requested_by: str | None
    status: Literal["pending", "approved"]
    decided_by: str | None
    created_at: datetime
    decided_at: datetime | None


class PluginApprovalList(Dto):
    items: list[PluginApprovalDto]


class PluginKept(Dto):
    agents: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class PluginUninstallResult(Dto):
    """What an uninstall removed and what it kept as the organisation's own (ADR 0040, ADR 0093)."""

    name: str
    version: str
    removed: PluginKept
    kept: PluginKept
