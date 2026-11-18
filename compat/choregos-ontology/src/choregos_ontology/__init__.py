"""Deprecated: `choregos_ontology` is now `tranibor_ontology` (ADR 0176 R2); this module is that package."""

from tranibor_contracts._compat import install_alias

install_alias("choregos_ontology", "tranibor_ontology")
