"""Sidecar MCP `choregos-tools` : les seuls outils d'écriture d'un agent."""

from __future__ import annotations

from .server import McpServer
from .tools import TOOL_SCHEMAS, ToolContext

__version__ = "0.1.0"

__all__ = ["TOOL_SCHEMAS", "McpServer", "ToolContext"]
