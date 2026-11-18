"""Deprecated: `choregos_runner` is now `tranibor_runner` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_runner", "tranibor_runner")
