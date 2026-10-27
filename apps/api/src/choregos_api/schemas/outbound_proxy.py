# SPDX-License-Identifier: Apache-2.0
"""An organisation's outbound proxy (S40-26b, ADR 0087). The password is write-only: no DTO that is
returned has a field for it."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from .base import Dto

ProxyKind = Literal["web_fetch", "web_search", "knowledge_web", "search_engines"]
ENDPOINT_PATTERN = r"^[A-Za-z0-9.-]{1,253}:[0-9]{1,5}$"
ALL_KINDS: list[ProxyKind] = ["web_fetch", "web_search", "knowledge_web", "search_engines"]


class OutboundProxy(Dto):
    enabled: bool
    endpoint: str
    scheme: Literal["http", "https"]
    username: str
    password_set: bool
    applies_to: list[ProxyKind]


class OutboundProxyState(Dto):
    proxy: OutboundProxy | None = None
    updated_by: str | None = None
    updated_at: str | None = None


class OutboundProxyPut(Dto):
    enabled: bool
    endpoint: Annotated[str, Field(pattern=ENDPOINT_PATTERN)]
    scheme: Literal["http", "https"]
    username: Annotated[str, Field(max_length=512)]
    password: Annotated[str, Field(min_length=1, max_length=1024)] | None = Field(default=None, repr=False)
    applies_to: Annotated[list[ProxyKind], Field(min_length=1)] = Field(
        default_factory=lambda: list(ALL_KINDS)
    )


class OutboundProxyTest(Dto):
    ok: bool
    exit_ip: str | None = None
    country: str | None = None
    error: str | None = None
    elapsed_ms: int
    target: str


class RelayProxyCredentials(Dto):
    """The one answer with the password in clear: only the relay route returns it (ADR 0091 §7)."""

    endpoint: Annotated[str, Field(pattern=ENDPOINT_PATTERN)]
    scheme: Literal["http", "https"]
    username: Annotated[str, Field(max_length=512)]
    password: Annotated[str, Field(max_length=1024)] = Field(repr=False)


class RelayOutboundProxy(Dto):
    proxy: RelayProxyCredentials | None
