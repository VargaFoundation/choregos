"""Deprecated: `choregos_orchestrator` is now `tranibor_orchestrator` (ADR 0176 R2); an alias."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_orchestrator", "tranibor_orchestrator")
