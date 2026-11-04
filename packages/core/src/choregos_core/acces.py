# SPDX-License-Identifier: Apache-2.0
# lang: fr-ok-file alias:internal
"""Former module path of `choregos_core.access` (ADR 0098 §3 (a))."""

from __future__ import annotations

from .access import NATURES, AccessLine, AccessReport, access_report

Acces = AccessLine
RapportAcces = AccessReport
rapport_d_acces = access_report

__all__ = ["NATURES", "Acces", "RapportAcces", "rapport_d_acces"]
