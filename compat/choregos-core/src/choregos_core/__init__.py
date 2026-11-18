"""Deprecated: `choregos_core` is now `tranibor_core` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_core", "tranibor_core")
