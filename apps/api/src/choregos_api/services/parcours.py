# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.journey`, ADR 0098
"""The former name of `choregos_api.services.journey`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .journey import (
    arete_du_deplacement,
    journey,
    move_edge,
    parcours,
)

__all__ = [
    "arete_du_deplacement",
    "journey",
    "move_edge",
    "parcours",
]

replace_module(__name__, sys.modules["choregos_api.services.journey"])
