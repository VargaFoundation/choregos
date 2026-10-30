# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_adapters.families` (ADR 0098 §3 (a))."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .families import (
    FAMILLES,
    OPERATIONS_CONTROLE_D_ACCES,
    OPERATIONS_EXPEDITION,
    OPERATIONS_MARKETDATA,
    OPERATIONS_MDM,
)

__all__ = [
    "FAMILLES",
    "OPERATIONS_CONTROLE_D_ACCES",
    "OPERATIONS_EXPEDITION",
    "OPERATIONS_MARKETDATA",
    "OPERATIONS_MDM",
]

replace_module(__name__, sys.modules["choregos_adapters.families"])
