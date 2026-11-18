"""Deprecated: `choregos_api` is now `tranibor_api` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_api", "tranibor_api")
