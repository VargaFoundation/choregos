# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_adapters.fakes.demo_cd` (ADR 0098 §3 (a))."""

from __future__ import annotations

import sys

from choregos_core.compat import replace_module

from .demo_cd import OPERATIONS_CD_DE_DEMO, CdDeDemo, FakeCdDeDemo

__all__ = ["OPERATIONS_CD_DE_DEMO", "CdDeDemo", "FakeCdDeDemo"]

replace_module(__name__, sys.modules["choregos_adapters.fakes.demo_cd"])
