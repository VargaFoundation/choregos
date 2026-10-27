# SPDX-License-Identifier: Apache-2.0
"""Installed plugins (ADR 0093): the DTOs of `/orgs/{org}/plugins`, as the contract declares them."""

from __future__ import annotations

from datetime import datetime

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
