# SPDX-License-Identifier: Apache-2.0
"""The assertion keys of an organisation's MCP gateway (S46-07, ADR 0105): public data only."""

from __future__ import annotations

from .base import Dto


class McpGatewayAssertionKey(Dto):
    kid: str
    created_at: str
    current: bool


class McpGatewayAssertion(Dto):
    issuer: str
    jwks_url: str
    keys: list[McpGatewayAssertionKey]
