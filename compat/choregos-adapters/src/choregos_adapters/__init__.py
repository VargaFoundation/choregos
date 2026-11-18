"""Deprecated: `choregos_adapters` is now `tranibor_adapters` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_adapters", "tranibor_adapters")
