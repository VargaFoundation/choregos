"""Deprecated: `choregos_tools_mcp` is now `tranibor_tools_mcp` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_tools_mcp", "tranibor_tools_mcp")
