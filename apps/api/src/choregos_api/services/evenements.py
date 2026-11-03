# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal; former `services.events`, ADR 0098
"""The former name of `choregos_api.services.events`: the same module object, so a patch on one
is a patch on the other."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .events import (
    persist_event,
)

__all__ = [
    "persist_event",
]

replace_module(__name__, sys.modules["choregos_api.services.events"])
