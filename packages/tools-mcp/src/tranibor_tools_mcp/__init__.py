# SPDX-License-Identifier: Apache-2.0
"""The `choregos-tools` MCP sidecar: an agent's only write tools."""

from __future__ import annotations

from .server import McpServer
from .tools import TOOL_SCHEMAS, ToolContext

__version__ = "0.1.0"

__all__ = ["TOOL_SCHEMAS", "McpServer", "ToolContext"]
