"""Deprecated: `choregos_contracts` is now `tranibor_contracts` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_contracts", "tranibor_contracts")
