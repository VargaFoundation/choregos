"""Deprecated: `choregos_quant` is now `tranibor_quant` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_quant", "tranibor_quant")
