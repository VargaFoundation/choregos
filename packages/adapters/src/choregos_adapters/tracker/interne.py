# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_adapters.tracker.internal` (ADR 0098 §3 (a))."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .internal import InternalTracker

__all__ = ["InternalTracker"]

replace_module(__name__, sys.modules["choregos_adapters.tracker.internal"])
