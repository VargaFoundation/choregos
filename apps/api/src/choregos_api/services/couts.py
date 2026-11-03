# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.costs`, ADR 0098
"""The former name of `choregos_api.services.costs`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .costs import (
    dimensions_of,
    estimate_cost,
    record_cost,
)

__all__ = [
    "dimensions_of",
    "estimate_cost",
    "record_cost",
]

replace_module(__name__, sys.modules["choregos_api.services.costs"])
