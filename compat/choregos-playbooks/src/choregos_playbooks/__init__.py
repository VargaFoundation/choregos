"""Deprecated: `choregos_playbooks` is now `tranibor_playbooks` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_playbooks", "tranibor_playbooks")
