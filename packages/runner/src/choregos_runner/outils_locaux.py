# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_runner.local_tools` (ADR 0098 §3 (a))."""

from __future__ import annotations

from .local_tools import start_if_absent, stop

demarrer_si_absent = start_if_absent
arreter = stop

__all__ = ["arreter", "demarrer_si_absent"]
