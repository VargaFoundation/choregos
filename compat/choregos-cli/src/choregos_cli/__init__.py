"""Deprecated: `choregos_cli` is now `tranibor_cli` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_cli", "tranibor_cli")
