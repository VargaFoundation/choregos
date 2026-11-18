"""Deprecated: `choregos_outpost` is now `tranibor_outpost` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_outpost", "tranibor_outpost")
