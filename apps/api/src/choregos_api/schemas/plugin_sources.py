# SPDX-License-Identifier: Apache-2.0
"""The deployment's plugin sources as the API shows them (ADR 0093, `PluginSources`)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import Dto


class PluginTrustDto(Dto):
    mode: Literal["keyless", "key", "none"]
    issuer: str | None = None
    identity: str | None = None
    key_fingerprint: str | None = None


class PluginSourceVersionDto(Dto):
    version: str
    core: str
    admissible: bool


class PluginSourceEntryDto(Dto):
    name: str
    description: str | None
    versions: list[PluginSourceVersionDto]


class PluginSourceDto(Dto):
    name: str
    index: str
    trust: PluginTrustDto
    plugins: list[PluginSourceEntryDto] = Field(default_factory=list)
    error: str | None


class PluginSourcesDto(Dto):
    sources: list[PluginSourceDto]
