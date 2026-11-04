# SPDX-License-Identifier: Apache-2.0
"""The `mcp` connector type (ADR 0034): a real MCP server, reached by the platform."""

from .client import (
    CLIENT_VERSIONS,
    HeadersProvider,
    McpClient,
    McpError,
    McpTool,
    schema_digest,
)

__all__ = [
    "CLIENT_VERSIONS",
    "HeadersProvider",
    "McpClient",
    "McpError",
    "McpTool",
    "schema_digest",
]
