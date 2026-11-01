# SPDX-License-Identifier: Apache-2.0
"""An organisation's MCP gateway (S46-07, S46-08, ADR 0105). The credential is write-only: no DTO
that is returned has a field for it, and the assertion keys are public data only."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from .base import Dto

HEADER_NAME_PATTERN = r"^[A-Za-z0-9-]{1,64}$"
ENDPOINT_PATTERN = r"^https?://[^\s@?#]+$"
GatewayMode = Literal["off", "available", "required"]
GatewayRouting = Literal["path", "header"]
GatewayAuthType = Literal["bearer", "header", "oauth_client_credentials", "jwt_assertion"]
HeaderName = Annotated[str, Field(pattern=HEADER_NAME_PATTERN)]
Endpoint = Annotated[str, Field(max_length=2000, pattern=ENDPOINT_PATTERN)]


class McpGatewayAssertionKey(Dto):
    kid: str
    created_at: str
    current: bool


class McpGatewayAssertion(Dto):
    issuer: str
    jwks_url: str
    keys: list[McpGatewayAssertionKey]


class McpGatewayAuth(Dto):
    """How Choregos authenticates to the gateway. The credential itself is `McpGatewayPut.secret`."""

    type: GatewayAuthType
    header_name: HeaderName | None = None
    token_url: Annotated[str, Field(max_length=2000)] | None = None
    client_id: Annotated[str, Field(max_length=256)] | None = None
    scope: Annotated[str, Field(max_length=1024)] | None = None
    resource: Annotated[str, Field(max_length=2000)] | None = None
    token_audience: Annotated[str, Field(max_length=512)] | None = None


class McpGateway(Dto):
    mode: GatewayMode
    endpoint: Endpoint
    routing: GatewayRouting
    server_header: HeaderName | None
    auth: McpGatewayAuth
    audience: str | None
    identity_header: HeaderName | None
    timeout_s: Annotated[float, Field(ge=1, le=120)]
    secret_set: bool


class McpGatewayState(Dto):
    gateway: McpGateway | None = None
    updated_by: str | None = None
    updated_at: str | None = None
    assertion: McpGatewayAssertion


class McpGatewayPut(Dto):
    mode: GatewayMode
    endpoint: Endpoint
    routing: GatewayRouting
    server_header: HeaderName | None = None
    auth: McpGatewayAuth
    audience: Annotated[str, Field(max_length=512)] | None = None
    identity_header: HeaderName | None = None
    timeout_s: Annotated[float, Field(ge=1, le=120)] = 30
    secret: Annotated[str, Field(min_length=1, max_length=4096)] | None = Field(default=None, repr=False)
    allow_egress: bool = False


class McpGatewayTestRequest(Dto):
    server: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]


class McpGatewayTest(Dto):
    ok: bool
    server: str
    protocol_version: str | None
    server_info: dict[str, object] | None
    tools: int | None
    elapsed_ms: Annotated[int, Field(ge=0)]
    error: str | None
