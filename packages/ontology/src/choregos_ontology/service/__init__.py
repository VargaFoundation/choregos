# SPDX-License-Identifier: Apache-2.0
"""The ontology served by the Choregos core, as a plugin (trial, items 2 and 3).

This sub-package is the trial's "service" part: it talks to PostgreSQL and to the core's API
(`choregos-api`, extra `service`). The rest of `choregos_ontology` — model, loading, validation,
compilation, observations — has no I/O.

The plugin goes through the core's seams, and through them only:
- `choregos.plugins` → `plugin.register()` declares its routes and its run tools;
- `choregos.migrations` → `plugin.MIGRATIONS`, an Alembic branch that creates ITS tables, under RLS.

Nothing is imported from the core when this module loads: `register()` imports what it needs.
"""
