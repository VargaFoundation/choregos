# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_core.exit_codes` (ADR 0098 §3 (a))."""

from __future__ import annotations

from .exit_codes import Exit, explanation

explication = explanation

__all__ = ["Exit", "explication"]
