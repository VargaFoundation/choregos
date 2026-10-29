# SPDX-License-Identifier: Apache-2.0
"""The `mcp` connector type (ADR 0034): a real MCP server, reached by the platform."""

from .client import (
    CLIENT_VERSIONS,
    ClientMcp,  # lang: fr-ok alias:internal
    ErreurMcp,  # lang: fr-ok alias:internal
    HeadersProvider,
    McpClient,
    McpError,
    McpTool,
    OutilMcp,  # lang: fr-ok alias:internal
    empreinte_du_schema,  # lang: fr-ok alias:internal
    schema_digest,
)

__all__ = [
    "CLIENT_VERSIONS",
    "ClientMcp",  # lang: fr-ok alias:internal
    "ErreurMcp",  # lang: fr-ok alias:internal
    "HeadersProvider",
    "McpClient",
    "McpError",
    "McpTool",
    "OutilMcp",  # lang: fr-ok alias:internal
    "empreinte_du_schema",  # lang: fr-ok alias:internal
    "schema_digest",
]
