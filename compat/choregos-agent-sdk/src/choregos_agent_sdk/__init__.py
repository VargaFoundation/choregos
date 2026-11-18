"""Deprecated: `choregos_agent_sdk` is now `tranibor_agent_sdk` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_agent_sdk", "tranibor_agent_sdk")
