# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:external — python -m choregos_api.migrer, removed in 0.22.0 (ADR 0098)
"""The former name of `choregos_api.migrate`: `python -m choregos_api.migrer` still runs the migrations."""

from __future__ import annotations

from .migrate import MIGRATIONS, MIGRATIONS_GROUP, configuration, extension_locations, main

__all__ = ["MIGRATIONS", "configuration", "main"]

emplacements_des_greffons = extension_locations  # lang: fr-ok alias:external
GROUPE_DE_MIGRATIONS = MIGRATIONS_GROUP  # lang: fr-ok alias:external

if __name__ == "__main__":
    main()
