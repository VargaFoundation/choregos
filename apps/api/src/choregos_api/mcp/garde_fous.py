# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal — former MCP door module (ADR 0098), removed by S45-48
"""The former name of `choregos_api.mcp.limits`: the same module object under its English name."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .limits import (
    CALLS_PER_MINUTE,
    MAX_SIZE,
    RATE_LIMITER,
    WRITES_PER_DAY,
    truncate,
    writes_today,
)

__all__: list[str] = []

APPELS_PAR_MINUTE = CALLS_PER_MINUTE  # lang: fr-ok alias:internal
ECRITURES_PAR_JOUR = WRITES_PER_DAY  # lang: fr-ok alias:internal
TAILLE_MAX = MAX_SIZE  # lang: fr-ok alias:internal
LIMITEUR = RATE_LIMITER  # lang: fr-ok alias:internal
tronquer = truncate  # lang: fr-ok alias:internal
ecritures_du_jour = writes_today  # lang: fr-ok alias:internal

replace_module(__name__, sys.modules["choregos_api.mcp.limits"])
