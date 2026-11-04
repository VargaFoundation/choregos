# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.memory`, ADR 0098
"""The former name of `choregos_api.services.memory`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .memory import (
    MIN_TICKETS_PAR_GROUPE,
    MIN_WORK_ITEMS_PER_GROUP,
    _ab_verdict,
    _window_stats,
    memory_ab_comparison,
)

__all__ = [
    "MIN_TICKETS_PAR_GROUPE",
    "MIN_WORK_ITEMS_PER_GROUP",
    "_ab_verdict",
    "_window_stats",
    "memory_ab_comparison",
]

replace_module(__name__, sys.modules["choregos_api.services.memory"])
